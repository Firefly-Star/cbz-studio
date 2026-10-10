#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
cbz_editor.py —— 边看边改的 .cbz 阅读 / 编辑器（Tkinter + Pillow）

打开 cbz 就能翻页阅读，同时可以直接在同一个窗口里：
删页 / 恢复 / 插入图片 / 旋转 / 上下移动 / 改章节目录 / 设为封面。
所有改动先在内存里打标记（列表里用 ✗ ＋ ★ 显示），按 Ctrl+S 才落盘，
任何一步都能 Ctrl+Z 撤销。

运行:
    python cbz_editor.py                        # 启动后从「打开」按钮选文件
    python cbz_editor.py "manga\某漫画.cbz"      # 直接打开
    python cbz_editor.py --selftest "x.cbz"     # 无界面自检（不弹窗，供排错用）

快捷键:
    ← / →            上一页 / 下一页（日漫右开模式下方向自动反转）
    空格 / Shift+空格  下一页 / 上一页
    PgDn / PgUp      下一话 / 上一话
    Home / End       第一页 / 最后一页
    Delete           标记删除当前页        恢复: Ctrl+Z 或点「恢复本页」
    Ctrl+Z / Ctrl+Y  撤销 / 重做
    Insert           插入图片（可多选，插在当前页之后）
    R / Shift+R      顺时针 / 逆时针旋转 90°
    Alt+↑ / Alt+↓    当前页上移 / 下移
    T                改当前页（或列表里连续选中的页）的章节目录
    C                当前页设为封面（移到最前并改名 000_cover.*）
    D                双页跨页显示        M  切换日漫右开
    F                适应整页 / 适应宽度 / 原始大小 循环
    G                跳页               Ctrl+S 保存   Ctrl+Shift+S 另存为
    Ctrl+O 打开       Ctrl+Q 退出
    滚轮 翻页         Ctrl+滚轮 缩放     Shift+滚轮 上下滚     左键拖动 平移
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from pathlib import Path, PurePosixPath

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

LANCZOS = Image.Resampling.LANCZOS
IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif",
             ".bmp", ".avif", ".tif", ".tiff"}
JPEG_EXT = {".jpg", ".jpeg", ".jpe"}
FONT = ("Microsoft YaHei UI", 9)
BG = "#1f1f22"
FG = "#e8e8ea"


# ------------------------------------------------------------------ 小工具

def is_image(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in IMAGE_EXT


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def entry_dir(name: str) -> str:
    d = str(PurePosixPath(name).parent)
    return "" if d == "." else d


def with_dir(name: str, d: str) -> str:
    base = PurePosixPath(name).name
    return f"{d.strip('/')}/{base}" if d.strip("/") else base


def fmt_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}GB"


# ------------------------------------------------------------------ 数据模型

class Entry:
    """一个页面。删除/旋转只是打标记，保存时才真正落盘。"""

    __slots__ = ("zip_name", "disk_path", "out_name", "rotation", "deleted", "is_new")

    def __init__(self, zip_name=None, disk_path=None, out_name=None):
        self.zip_name = zip_name          # 包内原始条目名（新插入的为 None）
        self.disk_path = disk_path        # 新插入图片的磁盘路径
        self.out_name = out_name or zip_name
        self.rotation = 0                 # 顺时针角度
        self.deleted = False
        self.is_new = zip_name is None

    @property
    def cache_key(self):
        return self.disk_path if self.is_new else self.zip_name


class Doc:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if not zipfile.is_zipfile(self.path):
            raise ValueError(f"不是有效的 zip/cbz: {self.path}")
        self.zip = zipfile.ZipFile(self.path)
        self.keep = []          # 原样保留的非图片条目（ComicInfo.xml 等）
        self.entries = []
        for e in self.zip.infolist():
            if e.is_dir():
                continue
            if is_image(e.filename):
                self.entries.append(Entry(zip_name=e.filename, out_name=e.filename))
            else:
                self.keep.append(e.filename)
        self.rtl = self._detect_rtl()

    def _detect_rtl(self) -> bool:
        name = next((n for n in self.keep if n.lower().endswith("comicinfo.xml")), None)
        if not name:
            return True
        try:
            root = ET.fromstring(self.zip.read(name).decode("utf-8-sig"))
            node = root.find("Manga")
            return bool(node is not None and node.text and "RightToLeft" in node.text)
        except Exception:
            return True

    def live(self):
        return [e for e in self.entries if not e.deleted]

    def read(self, e: Entry) -> bytes:
        if e.is_new:
            return Path(e.disk_path).read_bytes()
        return self.zip.read(e.zip_name)

    def close(self):
        try:
            self.zip.close()
        except Exception:
            pass


# ------------------------------------------------------------------ 保存

def _final_names(doc, renumber, digits, start):
    live = doc.live()
    if not renumber:
        return [e.out_name for e in live]
    counters = {}
    names = []
    for e in live:
        d = entry_dir(e.out_name)
        base = PurePosixPath(e.out_name).name
        ext = PurePosixPath(e.out_name).suffix.lower()
        if base.lower().startswith("000_cover"):
            names.append(e.out_name)
            continue
        i = counters.get(d, start)
        counters[d] = i + 1
        names.append(f"{d}/{i:0{digits}d}{ext}" if d else f"{i:0{digits}d}{ext}")
    return names


def _encode(e: Entry, img: Image.Image) -> bytes:
    ext = PurePosixPath(e.out_name).suffix.lower()
    buf = io.BytesIO()
    if ext in JPEG_EXT:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if getattr(img, "format", None) == "JPEG" and img.mode == "RGB":
            img.save(buf, "JPEG", quality="keep", subsampling="keep")
        else:
            # 旋转后是新对象，format 已丢失，只能重新编码：4:4:4 + q95 尽量少损
            img.save(buf, "JPEG", quality=95, subsampling=0)
    elif ext == ".webp":
        img.save(buf, "WEBP", quality=95)
    elif ext == ".gif":
        img.convert("P").save(buf, "GIF")
    else:
        if img.mode not in ("RGB", "RGBA", "L", "P"):
            img = img.convert("RGBA")
        img.save(buf, "PNG")
    return buf.getvalue()


def save_doc(doc: Doc, out_path, renumber=False, digits=4, start=0, progress=None):
    """把内存状态写成新的 cbz。返回实际写出的页数。"""
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    live = doc.live()
    names = _final_names(doc, renumber, digits, start)
    if len(set(names)) != len(names):
        dup = [n for n, c in Counter(names).items() if c > 1][:3]
        raise ValueError(f"保存后出现重名条目: {dup}（检查章节目录/重编号设置）")

    fd, tmp = tempfile.mkstemp(suffix=".tmp.cbz", dir=str(out_path.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
            # 非图片条目（含更新后的 PageCount）
            for name in doc.keep:
                data = doc.zip.read(name)
                if name.lower().endswith("comicinfo.xml"):
                    data = _patch_pagecount(data, len(live))
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, data)
            total = len(live)
            for i, (e, name) in enumerate(zip(live, names)):
                ct = zipfile.ZIP_STORED if is_image(name) else zipfile.ZIP_DEFLATED
                if e.rotation % 360 == 0:
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = ct
                    if e.is_new:
                        with Path(e.disk_path).open("rb") as s, zout.open(info, "w") as d:
                            shutil.copyfileobj(s, d, 256 * 1024)
                    else:
                        with doc.zip.open(e.zip_name) as s, zout.open(info, "w") as d:
                            shutil.copyfileobj(s, d, 256 * 1024)
                else:
                    img = Image.open(io.BytesIO(doc.read(e)))
                    img.load()
                    if e.rotation % 360:
                        img = img.rotate(-(e.rotation % 360), expand=True)
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = ct
                    zout.writestr(info, _encode(e, img))
                if progress and (i % 16 == 0 or i == total - 1):
                    progress(i + 1, total)
        doc.zip.close()   # 原地保存前必须释放句柄，否则 Windows 拒绝替换
        if doc.path.exists() and doc.path == out_path:
            bak = out_path.with_name(out_path.name + ".bak")
            if not bak.exists():
                shutil.copy2(out_path, bak)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return len(live)


def _patch_pagecount(data: bytes, count: int) -> bytes:
    try:
        root = ET.fromstring(data.decode("utf-8-sig"))
        node = root.find("PageCount")
        if node is None:
            node = ET.SubElement(root, "PageCount")
        node.text = str(count)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    except Exception:
        return data


# ------------------------------------------------------------------ 界面

class App(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("cbz 阅读 / 编辑器")
        self.minsize(820, 560)
        self.configure(bg=BG)
        self.doc: Doc | None = None
        self.current = 0
        self.spread = False
        self.fit_mode = "page"          # page | width | none
        self.zoom = 1.0
        self.dirty = False
        self.last_save_path = None
        self.undo_stack = []
        self.redo_stack = []
        self._photo = None
        self._pil_cache = OrderedDict()
        self._syncing = False
        self._drag = None
        self._build_ui()
        self._bind_keys()
        if path:
            self.load(path)

    # -------------------------------------------------- 构件
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=FONT, padding=(5, 3))
        style.configure("TLabel", font=FONT)
        style.configure("Treeview", font=FONT, rowheight=21, background="#26262b",
                        fieldbackground="#26262b", foreground=FG)
        style.configure("Treeview.Heading", font=FONT)
        style.map("Treeview", background=[("selected", "#3b6ea5")])

        bar = tk.Frame(self, bg="#2a2a30")
        bar.pack(side="top", fill="x")
        bar2 = tk.Frame(self, bg="#2a2a30")
        bar2.pack(side="top", fill="x")
        bar3 = tk.Frame(self, bg="#2a2a30")
        bar3.pack(side="top", fill="x")

        def btn(parent, text, cmd, width=None):
            b = ttk.Button(parent, text=text, command=cmd, width=width)
            b.pack(side="left", padx=1, pady=3)
            return b

        def sep(parent):
            tk.Label(parent, text="│", bg="#2a2a30", fg="#666").pack(side="left", padx=3)

        btn(bar, "打开", self.ask_open)
        btn(bar, "保存", self.save)
        btn(bar, "另存为", self.save_as)
        sep(bar)
        btn(bar, "撤销", self.undo)
        btn(bar, "重做", self.redo)
        self.var_renumber = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text="保存时重编号", variable=self.var_renumber,
                       bg="#2a2a30", fg=FG, selectcolor="#2a2a30",
                       activebackground="#2a2a30", activeforeground=FG,
                       font=FONT).pack(side="left", padx=6)
        btn(bar, "快捷键", self.show_help, width=7)
        self.lbl_file = tk.Label(bar, text="未打开文件", bg="#2a2a30", fg="#9ad",
                                 font=FONT)
        self.lbl_file.pack(side="right", padx=8)

        btn(bar2, "◀ 上一页", lambda: self.go(-1))
        btn(bar2, "下一页 ▶", lambda: self.go(1))
        sep(bar2)
        btn(bar2, "删除", self.delete_page)
        btn(bar2, "恢复", self.undelete_page)
        btn(bar2, "插入图片", self.insert_files)
        sep(bar2)
        btn(bar2, "↺ 左转", lambda: self.rotate_page(-90))
        btn(bar2, "↻ 右转", lambda: self.rotate_page(90))
        sep(bar2)
        btn(bar2, "上移", lambda: self.move_page(-1))
        btn(bar2, "下移", lambda: self.move_page(1))
        sep(bar2)
        btn(bar2, "改章节", self.ask_set_dir)
        btn(bar2, "设封面", self.set_cover)

        btn(bar3, "跳页", self.ask_goto)
        sep(bar3)
        btn(bar3, "适应整页", lambda: self.set_fit("page"))
        btn(bar3, "适应宽度", lambda: self.set_fit("width"))
        btn(bar3, "原大小", lambda: self.set_fit("none"))
        btn(bar3, "缩小", lambda: self.zoom_by(0.8), width=4)
        btn(bar3, "放大", lambda: self.zoom_by(1.25), width=4)
        sep(bar3)
        self.btn_spread = ttk.Button(bar3, text="双页", command=self.toggle_spread)
        self.btn_spread.pack(side="left", padx=1, pady=3)
        self.btn_rtl = ttk.Button(bar3, text="日漫右开", command=self.toggle_rtl)
        self.btn_rtl.pack(side="left", padx=1, pady=3)

        self.progress = ttk.Progressbar(self, mode="determinate")

        main = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=4,
                              sashrelief="flat")
        main.pack(side="top", fill="both", expand=True)
        self._main = main

        left = tk.Frame(main, bg=BG)
        self.tree = ttk.Treeview(left, columns=("page",), show="tree",
                                 selectmode="extended")
        self.tree.column("#0", width=380, minwidth=160)
        vs = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("deleted", foreground="#8a5a5a")
        self.tree.tag_configure("new", foreground="#6fbf7f")
        self.tree.tag_configure("current", background="#3b6ea5", foreground="#fff")
        main.add(left, width=400)

        right = tk.Frame(main, bg=BG)
        self.canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        hb = ttk.Scrollbar(right, orient="horizontal", command=self.canvas.xview)
        vb = ttk.Scrollbar(right, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hb.set, yscrollcommand=vb.set)
        vb.pack(side="right", fill="y")
        hb.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        main.add(right)

        self.status = tk.Label(self, text="就绪", bg="#2a2a30", fg=FG, font=FONT,
                               anchor="w", padx=8)
        # before=main：先给状态栏留出高度，否则会被 expand 的画布挤成 0 高
        self.status.pack(side="bottom", fill="x", before=main)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.canvas.bind("<Configure>", lambda e: self.render())
        self._bars = (bar, bar2, bar3)
        self._fit_window()

    def _fit_window(self):
        """按工具栏实际需要的宽度定窗口大小，并在高 DPI 下自动缩小按钮字号。"""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(940, sh - 100)
        size = 9
        for size in (9, 8, 7):
            ttk.Style(self).configure("TButton", font=("Microsoft YaHei UI", size))
            self.update_idletasks()
            need = max(b.winfo_reqwidth() for b in self._bars) + 24
            if need <= sw - 40 or size == 7:
                break
        self.geometry(f"{min(need, sw - 40)}x{h}")

    def _typing(self) -> bool:
        """焦点在输入框里时不响应单键快捷键（否则对话框里打字会误触）。"""
        w = self.focus_get()
        if w is None:
            return False
        try:
            return w.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox")
        except Exception:
            return False

    def _key(self, fn):
        def handler(_evt=None):
            if not self._typing():
                fn()
        return handler

    def _bind_keys(self):
        b = self.bind_all
        for seq, fn in (
            ("<Left>", lambda: self.go(-1 if self.rtl else 1)),
            ("<Right>", lambda: self.go(1 if self.rtl else -1)),
            ("<space>", lambda: self.go(1)),
            ("<Shift-space>", lambda: self.go(-1)),
            ("<Prior>", lambda: self.jump_chapter(-1)),
            ("<Next>", lambda: self.jump_chapter(1)),
            ("<Home>", self.go_first),
            ("<End>", self.go_last),
            ("<Delete>", self.delete_page),
            ("<Insert>", self.insert_files),
            ("<Control-z>", self.undo),
            ("<Control-y>", self.redo),
            ("<Control-Shift-Z>", self.redo),
            ("<Control-s>", lambda: self.save()),
            ("<Control-Shift-S>", lambda: self.save_as()),
            ("<Control-o>", self.ask_open),
            ("<Control-q>", self.on_close),
            ("<KeyPress-r>", lambda: self.rotate_page(90)),
            ("<KeyPress-R>", lambda: self.rotate_page(-90)),
            ("<KeyPress-t>", self.ask_set_dir),
            ("<KeyPress-c>", self.set_cover),
            ("<KeyPress-d>", self.toggle_spread),
            ("<KeyPress-m>", self.toggle_rtl),
            ("<KeyPress-f>", self.cycle_fit),
            ("<KeyPress-g>", self.ask_goto),
            ("<Alt-Up>", lambda: self.move_page(-1)),
            ("<Alt-Down>", lambda: self.move_page(1)),
            ("<plus>", lambda: self.zoom_by(1.25)),
            ("<equal>", lambda: self.zoom_by(1.25)),
            ("<minus>", lambda: self.zoom_by(0.8)),
        ):
            b(seq, self._key(fn))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------- 打开 / 关闭
    def ask_open(self):
        if not self._confirm_discard():
            return
        p = filedialog.askopenfilename(
            title="打开 cbz", filetypes=[("漫画包", "*.cbz *.zip"), ("所有文件", "*.*")])
        if p:
            self.load(p)

    def load(self, path):
        try:
            doc = Doc(path)
        except Exception as ex:
            messagebox.showerror("打开失败", str(ex))
            return
        if self.doc:
            self.doc.close()
        self.doc = doc
        self.rtl = doc.rtl
        self.btn_rtl.configure(text="日漫右开 ✓" if self.rtl else "日漫右开")
        self.current = 0
        self._pil_cache.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.last_save_path = None
        self.title(f"cbz 阅读 / 编辑器 — {Path(path).name}")
        self.lbl_file.configure(text=f"{Path(path).name}  ({fmt_size(Path(path).stat().st_size)})")
        self.rebuild_tree()
        self.set_current(0)
        self.status.configure(text=f"已打开，共 {len(self.doc.live())} 页 / "
                                   f"{len(self.doc.entries)} 条目")
        # 窗口映射完成后重新同步：否则左栏可能没滚到当前页，
        # 且首帧渲染用的是未成形的画布尺寸
        self.after(80, self._post_load)

    def _post_load(self):
        if not self.doc:
            return
        self.sync_tree()
        self.render()
        self.update_status()

    def show_help(self):
        messagebox.showinfo(
            "快捷键",
            "← / →         上一页 / 下一页（日漫右开时方向反转）\n"
            "空格 / Shift+空格   下一页 / 上一页\n"
            "PgDn / PgUp   下一话 / 上一话\n"
            "Home / End    第一页 / 最后一页\n"
            "Delete        标记删除当前页（Ctrl+Z 撤销）\n"
            "Insert        插入图片（可多选，插在当前页之后）\n"
            "R / Shift+R   顺时针 / 逆时针旋转 90°\n"
            "Alt+↑ / Alt+↓ 当前页上移 / 下移\n"
            "T             改当前页（或列表选中的连续页）的章节目录\n"
            "C             当前页设为封面（移到最前，改名 000_cover.*）\n"
            "D             双页跨页显示          M  日漫右开开关\n"
            "F             适应整页 / 适应宽度 / 原大小\n"
            "G             跳页                +/- 缩放\n"
            "Ctrl+S 保存（首次会另存为 .edited.cbz）\n"
            "Ctrl+Shift+S 另存为   Ctrl+O 打开   Ctrl+Q 退出\n"
            "滚轮 翻页    Ctrl+滚轮 缩放    Shift+滚轮 上下滚    左键拖动 平移")

    def on_close(self):
        if not self._confirm_discard():
            return
        if self.doc:
            self.doc.close()
        self.destroy()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel("未保存", "有未保存的改动，要先保存吗？")
        if r is None:
            return False
        if r:
            return self.save()
        return True

    # -------------------------------------------------- 导航
    def live_indices(self):
        return [i for i, e in enumerate(self.doc.entries) if not e.deleted] if self.doc else []

    @property
    def step(self):
        return 2 if self.spread else 1

    def go(self, delta):
        live = self.live_indices()
        if not live:
            return
        if self.current not in live:
            self.set_current(live[0])
            return
        pos = live.index(self.current)
        self.set_current(live[min(max(pos + delta * self.step, 0), len(live) - 1)])

    def go_first(self):
        live = self.live_indices()
        if live:
            self.set_current(live[0])

    def go_last(self):
        live = self.live_indices()
        if live:
            self.set_current(live[-1])

    def jump_chapter(self, direction):
        live = self.live_indices()
        if not live:
            return
        pos = live.index(self.current) if self.current in live else 0
        cur = entry_dir(self.doc.entries[live[pos]].out_name)
        if direction > 0:
            for j in range(pos + 1, len(live)):
                if entry_dir(self.doc.entries[live[j]].out_name) != cur:
                    self.set_current(live[j])
                    return
            self.set_current(live[-1])
        else:
            for j in range(pos - 1, -1, -1):
                d = entry_dir(self.doc.entries[live[j]].out_name)
                if d != cur:
                    k = j
                    while k - 1 >= 0 and entry_dir(self.doc.entries[live[k - 1]].out_name) == d:
                        k -= 1
                    self.set_current(live[k])
                    return
            self.set_current(live[0])

    def ask_goto(self):
        if not self.doc:
            return
        live = self.live_indices()
        page = simpledialog.askinteger(
            "跳页", f"页码 (1-{len(live)})", parent=self, minvalue=1, maxvalue=len(live))
        if page:
            self.set_current(live[page - 1])

    def set_current(self, idx, sync=True):
        if not self.doc:
            return
        self.current = max(0, min(idx, len(self.doc.entries) - 1))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        if sync:
            self.sync_tree()
        self.render()
        self.update_status()

    def normalize_current(self):
        live = self.live_indices()
        if not live:
            return
        if self.current not in live:
            nxt = next((i for i in live if i > self.current), live[-1])
            self.current = nxt

    def _on_tree_select(self, _evt=None):
        if self._syncing or not self.doc:
            return
        sel = [s for s in self.tree.selection() if s.startswith("e::")]
        if not sel:
            return
        self.set_current(int(sel[0].split("::")[1]), sync=False)

    def view_indices(self):
        live = self.live_indices()
        if not live:
            return []
        if self.current not in live:
            return [live[0]]
        pos = live.index(self.current)
        if self.spread and pos + 1 < len(live):
            return [live[pos], live[pos + 1]]
        return [live[pos]]

    # -------------------------------------------------- 渲染
    def load_pil(self, idx) -> Image.Image:
        e = self.doc.entries[idx]
        key = (e.cache_key, e.rotation % 360)
        if key in self._pil_cache:
            self._pil_cache.move_to_end(key)
            return self._pil_cache[key]
        img = Image.open(io.BytesIO(self.doc.read(e)))
        img.load()
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")
        if e.rotation % 360:
            img = img.rotate(-(e.rotation % 360), expand=True)
        self._pil_cache[key] = img
        if len(self._pil_cache) > 8:
            self._pil_cache.popitem(last=False)
        return img

    def render(self):
        self.canvas.delete("all")
        if not self.doc:
            return
        idxs = self.view_indices()
        if not idxs:
            self.canvas.create_text(20, 20, anchor="nw", fill="#888",
                                    text="没有可显示的页面", font=FONT)
            return
        imgs = []
        for i in idxs:
            try:
                imgs.append(self.load_pil(i))
            except Exception as ex:
                self.status.configure(text=f"第 {i+1} 页读取失败: {ex}")
        if not imgs:
            return
        if len(imgs) == 2:
            h = max(im.height for im in imgs)
            w = sum(im.width for im in imgs)
            canvas_img = Image.new("RGB", (w, h), (255, 255, 255))
            if self.rtl:
                right, left = imgs[0], imgs[1]
            else:
                left, right = imgs[0], imgs[1]
            canvas_img.paste(left, (0, 0))
            canvas_img.paste(right, (left.width, 0))
            base = canvas_img
        else:
            base = imgs[0]

        cw = max(self.canvas.winfo_width(), 120)
        ch = max(self.canvas.winfo_height(), 120)
        if self.fit_mode == "page":
            scale = min(cw / base.width, ch / base.height)
        elif self.fit_mode == "width":
            scale = cw / base.width
        else:
            scale = 1.0
        scale *= self.zoom
        scale = max(0.02, min(scale, 8.0))
        tw, th = max(1, int(base.width * scale)), max(1, int(base.height * scale))
        disp = base.resize((tw, th), LANCZOS) if (tw, th) != base.size else base
        if disp.mode not in ("RGB", "RGBA"):
            disp = disp.convert("RGB")
        self._photo = ImageTk.PhotoImage(disp)
        ox = max(0, (cw - tw) // 2)
        oy = max(0, (ch - th) // 2)
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, max(cw, tw), max(ch, th)))
        self._scale_shown = scale

    def update_status(self):
        if not self.doc:
            return
        live = self.live_indices()
        pos = live.index(self.current) + 1 if self.current in live else 0
        e = self.doc.entries[self.current]
        img = self._pil_cache.get((e.cache_key, e.rotation % 360))
        size = f"{img.width}x{img.height}" if img else "-"
        marks = []
        marks.append("新插入" if e.is_new else "包内")
        if e.rotation % 360:
            marks.append(f"旋转{e.rotation % 360}°")
        if e.deleted:
            marks.append("已删除")
        self.status.configure(
            text=f"第 {pos}/{len(live)} 页 · {size} · 缩放 {getattr(self, '_scale_shown', 1)*100:.0f}% · "
                 f"目录 {entry_dir(e.out_name) or '(根)'} · {' / '.join(marks)} · "
                 f"待删 {sum(1 for x in self.doc.entries if x.deleted)} 页"
                 f"{' · 未保存*' if self.dirty else ''}")

    def rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.doc:
            return
        groups = OrderedDict()
        for i, e in enumerate(self.doc.entries):
            groups.setdefault(entry_dir(e.out_name), []).append(i)
        for d, idxs in groups.items():
            node = self.tree.insert("", "end", iid=f"dir::{d}",
                                    text=f"{d or '(根目录)'}  [{len(idxs)}]",
                                    open=True)
            for i in idxs:
                e = self.doc.entries[i]
                tag = "deleted" if e.deleted else ("new" if e.is_new else "")
                prefix = "✗ " if e.deleted else ("＋ " if e.is_new else "   ")
                self.tree.insert(node, "end", iid=f"e::{i}",
                                 text=f"{prefix}{i+1:>4}  {PurePosixPath(e.out_name).name}",
                                 tags=(tag,))
        self.sync_tree()

    def sync_tree(self):
        if not self.doc:
            return
        self._syncing = True
        try:
            iid = f"e::{self.current}"
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.see(iid)
                parent = self.tree.parent(iid)
                if parent:
                    self.tree.see(parent)   # 别把所属章节节点顶出视野
        finally:
            self._syncing = False

    def _reload_view(self):
        self.normalize_current()
        self.rebuild_tree()
        self.render()
        self.update_status()

    # -------------------------------------------------- 编辑操作
    def push_undo(self, undo_fn, redo_fn):
        self.undo_stack.append((undo_fn, redo_fn))
        if len(self.undo_stack) > 300:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True

    def undo(self):
        if not self.undo_stack:
            self.status.configure(text="没有可撤销的操作")
            return
        undo_fn, redo_fn = self.undo_stack.pop()
        undo_fn()
        self.redo_stack.append((undo_fn, redo_fn))
        self.dirty = True
        self._reload_view()

    def redo(self):
        if not self.redo_stack:
            self.status.configure(text="没有可重做的操作")
            return
        undo_fn, redo_fn = self.redo_stack.pop()
        redo_fn()
        self.undo_stack.append((undo_fn, redo_fn))
        self.dirty = True
        self._reload_view()

    def delete_page(self):
        if not self.doc:
            return
        live = self.live_indices()
        if len(live) <= 1:
            messagebox.showinfo("提示", "至少要保留一页")
            return
        e = self.doc.entries[self.current]
        if e.deleted:
            return
        e.deleted = True
        self.push_undo(lambda: setattr(e, "deleted", False),
                       lambda: setattr(e, "deleted", True))
        self._reload_view()

    def undelete_page(self):
        if not self.doc:
            return
        e = self.doc.entries[self.current]
        if not e.deleted:
            self.status.configure(text="当前页没有被删除")
            return
        e.deleted = False
        self.push_undo(lambda: setattr(e, "deleted", True),
                       lambda: setattr(e, "deleted", False))
        self._reload_view()

    def insert_files(self):
        if not self.doc:
            return
        paths = filedialog.askopenfilenames(
            title="插入图片（按文件名排序，插在当前页之后）",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.gif *.bmp *.tif *.tiff"),
                       ("所有文件", "*.*")])
        if not paths:
            return
        paths = sorted(paths, key=natural_key)
        d = entry_dir(self.doc.entries[self.current].out_name)
        new_entries = []
        for p in paths:
            name = PurePosixPath(p).name
            new_entries.append(Entry(disk_path=str(Path(p).resolve()),
                                     out_name=with_dir(name, d)))
        at = self.current + 1
        self.doc.entries[at:at] = new_entries

        def undo():
            for ne in new_entries:
                if ne in self.doc.entries:
                    self.doc.entries.remove(ne)

        def redo():
            self.doc.entries[at:at] = new_entries

        self.push_undo(undo, redo)
        self.current = at
        self._reload_view()
        self.status.configure(text=f"已插入 {len(new_entries)} 页到 {d or '(根目录)'}（未保存）")

    def rotate_page(self, delta):
        if not self.doc:
            return
        e = self.doc.entries[self.current]
        e.rotation = (e.rotation + delta) % 360
        self.push_undo(lambda: setattr(e, "rotation", (e.rotation - delta) % 360),
                       lambda: setattr(e, "rotation", (e.rotation + delta) % 360))
        self.render()
        self.update_status()

    def move_page(self, delta):
        if not self.doc:
            return
        i = self.current
        j = i + delta
        if j < 0 or j >= len(self.doc.entries):
            return
        lst = self.doc.entries
        lst[i], lst[j] = lst[j], lst[i]
        self.push_undo(lambda: lst.insert(i, lst.pop(j)),
                       lambda: lst.insert(j, lst.pop(i)))
        self.current = j
        self._reload_view()

    def set_cover(self):
        if not self.doc:
            return
        e = self.doc.entries[self.current]
        old_name = e.out_name
        ext = PurePosixPath(old_name).suffix.lower() or ".jpg"
        new_name = f"000_cover{ext}"
        old_idx = self.current
        lst = self.doc.entries
        lst.insert(0, lst.pop(old_idx))
        e.out_name = new_name

        def undo():
            lst.insert(old_idx, lst.pop(0))
            e.out_name = old_name

        def redo():
            lst.insert(0, lst.pop(old_idx))
            e.out_name = new_name

        self.push_undo(undo, redo)
        self.current = 0
        self._reload_view()
        self.status.configure(text=f"已设为封面: {new_name}")

    def ask_set_dir(self):
        if not self.doc:
            return
        sel = [int(s.split("::")[1]) for s in self.tree.selection() if s.startswith("e::")]
        targets = sel or [self.current]
        cur = entry_dir(self.doc.entries[targets[0]].out_name)
        d = simpledialog.askstring(
            "章节目录", f"把这 {len(targets)} 页放到哪个章节目录？\n"
                        f"（留空 = 根目录；例如 001_第01话）", initialvalue=cur, parent=self)
        if d is None:
            return
        d = d.strip().strip("/").replace("\\", "/")
        old = {i: self.doc.entries[i].out_name for i in targets}
        for i in targets:
            self.doc.entries[i].out_name = with_dir(self.doc.entries[i].out_name, d)

        def undo():
            for i, n in old.items():
                self.doc.entries[i].out_name = n

        def redo():
            for i in targets:
                self.doc.entries[i].out_name = with_dir(self.doc.entries[i].out_name, d)

        self.push_undo(undo, redo)
        self._reload_view()
        self.status.configure(text=f"{len(targets)} 页已归入 {d or '(根目录)'}（未保存）")

    # -------------------------------------------------- 显示设置
    def set_fit(self, mode):
        self.fit_mode = mode
        self.zoom = 1.0
        self.render()
        self.update_status()

    def cycle_fit(self):
        order = ["page", "width", "none"]
        self.set_fit(order[(order.index(self.fit_mode) + 1) % len(order)])

    def zoom_by(self, factor):
        self.zoom = max(0.1, min(self.zoom * factor, 8.0))
        if self.fit_mode != "none":
            self.fit_mode = "none"
            self.zoom = getattr(self, "_scale_shown", 1.0) * factor
        self.render()
        self.update_status()

    def toggle_spread(self):
        self.spread = not self.spread
        self.btn_spread.configure(text="双页 ✓" if self.spread else "双页")
        self.render()
        self.update_status()

    def toggle_rtl(self):
        self.rtl = not self.rtl
        self.btn_rtl.configure(text="日漫右开 ✓" if self.rtl else "日漫右开")
        self.render()

    def _on_wheel(self, evt):
        ctrl = (evt.state & 0x0004) != 0
        shift = (evt.state & 0x0001) != 0
        if ctrl:
            self.zoom_by(1.1 if evt.delta > 0 else 1 / 1.1)
        elif shift:
            self.canvas.yview_scroll(-1 if evt.delta > 0 else 1, "units")
        else:
            self.go(1 if evt.delta < 0 else -1)

    def _pan_start(self, evt):
        self._drag = (evt.x, evt.y)
        self.canvas.scan_mark(evt.x, evt.y)

    def _pan_move(self, evt):
        if self._drag:
            self.canvas.scan_dragto(evt.x, evt.y, gain=1)

    # -------------------------------------------------- 保存
    def save(self):
        if not self.doc:
            return True
        if self.last_save_path:
            return self._write(self.last_save_path)
        return self.save_as()

    def save_as(self):
        if not self.doc:
            return True
        default = self.doc.path.with_name(self.doc.path.stem + ".edited.cbz")
        p = filedialog.asksaveasfilename(
            title="另存为 cbz", defaultextension=".cbz",
            initialfile=default.name, initialdir=str(default.parent),
            filetypes=[("漫画包", "*.cbz"), ("所有文件", "*.*")])
        if not p:
            return False
        return self._write(p)

    def _write(self, path):
        path = Path(path)
        same = path.resolve() == self.doc.path
        if same and not messagebox.askyesno(
                "原地保存", f"将覆盖原文件：\n{path}\n\n会自动留一份 .bak 备份。继续？"):
            return False
        renumber = bool(self.var_renumber.get())
        self.progress.pack(side="bottom", fill="x", before=self.status)
        self.progress.configure(maximum=len(self.doc.live()), value=0)
        self.status.configure(text="正在保存…")
        self.update_idletasks()

        def prog(i, n):
            self.progress.configure(value=i)
            if i % 64 == 0:
                self.update_idletasks()

        try:
            n = save_doc(self.doc, path, renumber=renumber, progress=prog)
        except Exception as ex:
            self.progress.pack_forget()
            # save_doc 可能已经关掉了源句柄，重开以保证界面还能继续用
            try:
                self.doc.zip = zipfile.ZipFile(self.doc.path)
            except Exception:
                pass
            messagebox.showerror("保存失败", str(ex))
            self.status.configure(text=f"保存失败: {ex}")
            return False
        self.progress.pack_forget()
        self.dirty = False
        self.last_save_path = path
        self.load(path)                      # 重新载入，状态归零
        self.status.configure(text=f"已保存 {n} 页 -> {path}")
        return True


# ------------------------------------------------------------------ 自检

def selftest(path):
    """无界面驱动一遍主要功能，最后保存到临时文件并校验。"""
    import io
    ok = []
    app = App()
    app.withdraw()
    app.load(path)
    app.update()
    assert app.doc is not None
    n0 = len(app.doc.live())
    ok.append(f"载入 {Path(path).name}: {n0} 页 / {len(app.doc.entries)} 条目，"
              f"右开={app.rtl}")

    app.set_current(1)
    app.go(1)
    assert app.current == 2, app.current
    ok.append("翻页 → 第 3 页")

    app.toggle_spread()
    assert len(app.view_indices()) == 2
    ok.append("双页显示 → 2 页")
    app.toggle_spread()

    app.set_current(2)
    app.delete_page()
    assert len(app.doc.live()) == n0 - 1
    ok.append(f"删除 1 页 → {len(app.doc.live())} 页")
    app.undo()
    assert len(app.doc.live()) == n0
    ok.append("撤销删除 → 恢复")

    app.set_current(2)
    app.delete_page()
    rot_idx = 3
    app.set_current(rot_idx)
    before = app.load_pil(rot_idx).size
    app.rotate_page(90)
    after = app.load_pil(rot_idx).size
    assert after == before[::-1], (before, after)
    ok.append(f"旋转 90° → {before} 变 {after}")

    tmpdir = Path.cwd() / "_cbzed_selftest_tmp"
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True)
    newimg = tmpdir / "999_insert.jpg"
    Image.new("RGB", (700, 1000), (200, 40, 60)).save(newimg, "JPEG")
    app.set_current(4)
    app.doc.entries.insert(5, Entry(disk_path=str(newimg), out_name="999_insert.jpg"))
    app.dirty = True
    ok.append("插入 1 页 → 列表 +1")

    app.set_current(5)
    app.doc.entries[5].out_name = with_dir("999_insert.jpg", "099_新章节")
    ok.append("改章节目录 → 099_新章节/999_insert.jpg")

    app.set_current(6)
    app.move_page(-1)
    ok.append("上移一页")

    out = tmpdir / "selftest_out.cbz"
    n = save_doc(app.doc, out, renumber=True, digits=4)
    ok.append(f"保存 → {out.name} ({fmt_size(out.stat().st_size)}, {n} 页)")

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert z.testzip() is None, "zip 校验失败"
        assert len(names) == len(set(names)), "有重名条目"
        assert not any("\\" in n for n in names), "有反斜杠条目"
        assert "099_新章节/9999.jpg" in names or "099_新章节/0000.jpg" in names, names[:5]
        ci = z.read("ComicInfo.xml").decode("utf-8-sig")
        assert f"<PageCount>{n}</PageCount>" in ci, "PageCount 未更新"
        ok.append(f"校验通过: {len(names)} 条目, ComicInfo PageCount={n}")
        ch1 = [x for x in names if x.startswith("001_第01话/")]
        assert len(ch1) == 40, f"删除未落盘: {len(ch1)}"
        ok.append(f"删除已落盘: 001_第01话 由 41 页变 {len(ch1)} 页")
        imgs = [x for x in names if is_image(x)]
        w, h = Image.open(io.BytesIO(z.read(imgs[2]))).size
        assert w > h, f"旋转未落盘: {w}x{h}"
        ok.append(f"旋转已落盘: 第 3 页存成 {w}x{h}（横向）")
        assert "099_新章节/0000.jpg" in names, "插入页未落盘"
        ok.append("插入页已落盘: 099_新章节/0000.jpg")

    app.doc.close()
    app.destroy()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n".join("  ✓ " + s for s in ok))
    print("\n自检全部通过")
    return 0


def main():
    ap = argparse.ArgumentParser(description="cbz 阅读 / 编辑器（边看边改）")
    ap.add_argument("cbz", nargs="?", help="要打开的 .cbz")
    ap.add_argument("--selftest", action="store_true", help="无界面自检后退出")
    a = ap.parse_args()
    if a.selftest:
        if not a.cbz:
            print("用法: python cbz_editor.py --selftest 文件.cbz")
            return 2
        return selftest(a.cbz)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App(a.cbz)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
