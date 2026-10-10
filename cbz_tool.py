#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
cbz_tool.py —— 编辑 .cbz（其实是 zip 的漫画包）的命令行工具

只依赖标准库。默认输出到 <名字>.edited.cbz，绝不静默覆盖原文件；
用 --in-place 原地改写时会自动留一份 .bak 备份。

常用示例（Windows PowerShell）:
    python cbz_tool.py info    manga\sample.cbz
    python cbz_tool.py check   manga\sample.cbz
    python cbz_tool.py list    manga\sample.cbz --images-only
    python cbz_tool.py extract manga\sample.cbz -o out --match "001_*"
    python cbz_tool.py add     manga\sample.cbz new.jpg --into "001_第01话/" --at 20
    python cbz_tool.py remove  manga\sample.cbz "*0035.jpg" --dry-run
    python cbz_tool.py replace manga\sample.cbz "001_第01话/0003.jpg" fixed.jpg
    python cbz_tool.py rename  manga\sample.cbz --renumber --digits 4
    python cbz_tool.py sort    manga\sample.cbz --order-file order.txt --renumber
    python cbz_tool.py meta    manga\sample.cbz --show
    python cbz_tool.py meta    manga\sample.cbz --set Series=安達與島村 --set Volume=1
    python cbz_tool.py repack  outdir -o new.cbz
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path, PurePosixPath

IMAGE_EXT = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif", ".bmp",
    ".avif", ".jxl", ".tif", ".tiff", ".heic",
}

# 控制台是 GBK 时，✓ / ✗ 这类非 GBK 字符会让 print 抛 UnicodeEncodeError 直接崩掉，
# 这里统一放宽成替换字符，而不是让脚本死掉。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
JUNK_DIRS = ("__MACOSX/",)


# ---------------------------------------------------------------- 基础工具

def log(msg=""):
    print(msg)


def fmt_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}GB"


def is_image(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in IMAGE_EXT


def is_junk(name: str) -> bool:
    low = name.lower()
    return low in JUNK_NAMES or low.startswith(JUNK_DIRS) or low.endswith("/.ds_store")


def natural_key(s: str):
    """让 2.jpg 排在 10.jpg 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def norm_entry_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def match_any(name: str, patterns) -> bool:
    """按完整条目名或文件名匹配 glob。"""
    if not patterns:
        return False
    base = PurePosixPath(name).name
    for p in patterns:
        if fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(base, p):
            return True
    return False


def open_src(path):
    p = Path(path)
    if not p.is_file():
        sys.exit(f"[错误] 找不到文件: {p}")
    if not zipfile.is_zipfile(p):
        sys.exit(f"[错误] 不是有效的 zip/cbz: {p}")
    return zipfile.ZipFile(p, "r")


def resolve_out(src, args) -> Path:
    src = Path(src)
    if getattr(args, "in_place", False):
        return src
    if getattr(args, "output", None):
        return Path(args.output)
    return src.with_name(src.stem + ".edited" + src.suffix)


def build_zip(src, dst, writer):
    """把写入逻辑交给 writer(zout)，写临时文件后原子替换。"""
    src, dst = (Path(src) if src is not None else None), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".tmp.cbz", dir=str(dst.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
            writer(zout)
        if src is not None and Path(src).exists() and Path(src).resolve() == dst.resolve():
            bak = src.with_name(src.name + ".bak")
            if not bak.exists():
                shutil.copy2(src, bak)
                log(f"[备份] 原文件 -> {bak.name}")
            else:
                log(f"[备份] 已存在，未覆盖: {bak.name}")
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    log(f"[写出] {dst}  ({fmt_size(dst.stat().st_size)})")


def copy_entry(zin, e, zout, new_name=None, compress=None):
    name = new_name if new_name is not None else e.filename
    if e.is_dir():
        if not name.endswith("/"):
            name += "/"
        zout.writestr(zipfile.ZipInfo(name, date_time=e.date_time), b"")
        return
    ct = compress
    if ct is None:
        ct = zipfile.ZIP_STORED if is_image(name) else zipfile.ZIP_DEFLATED
    info = zipfile.ZipInfo(name, date_time=e.date_time)
    info.compress_type = ct
    info.external_attr = e.external_attr
    info.internal_attr = e.internal_attr
    info.create_system = e.create_system
    with zin.open(e) as s, zout.open(info, "w") as d:
        shutil.copyfileobj(s, d, 256 * 1024)


def add_file(zout, name, path, compress=None):
    p = Path(path)
    if not p.is_file():
        sys.exit(f"[错误] 找不到要加入的文件: {p}")
    ct = compress
    if ct is None:
        ct = zipfile.ZIP_STORED if is_image(name) else zipfile.ZIP_DEFLATED
    info = zipfile.ZipInfo(name, date_time=time.localtime(p.stat().st_mtime)[:6])
    info.compress_type = ct
    info.external_attr = (0o644 & 0xFFFF) << 16
    with p.open("rb") as s, zout.open(info, "w") as d:
        shutil.copyfileobj(s, d, 256 * 1024)


def image_entries(zin):
    return [e for e in zin.infolist() if not e.is_dir() and is_image(e.filename)]


# ---------------------------------------------------------------- 子命令

def cmd_info(args):
    with open_src(args.cbz) as z:
        infos = z.infolist()
        imgs = [e for e in infos if not e.is_dir() and is_image(e.filename)]
        others = [e for e in infos if not e.is_dir() and not is_image(e.filename)]
        log(f"文件      : {Path(args.cbz).resolve()}")
        log(f"大小      : {fmt_size(Path(args.cbz).stat().st_size)}")
        log(f"条目总数  : {len(infos)}")
        log(f"图片页数  : {len(imgs)}")
        log(f"非图片    : {len(others)} -> {[e.filename for e in others][:10]}")
        dirs = sorted({str(PurePosixPath(e.filename).parent) for e in imgs})
        log(f"章节目录  : {len(dirs)} 个")
        for d in dirs[:20]:
            n = sum(1 for e in imgs if str(PurePosixPath(e.filename).parent) == d)
            log(f"    {d}/  ({n} 页)")
        if len(dirs) > 20:
            log(f"    ... 其余 {len(dirs) - 20} 个")
        exts = {}
        for e in imgs:
            exts[PurePosixPath(e.filename).suffix.lower()] = \
                exts.get(PurePosixPath(e.filename).suffix.lower(), 0) + 1
        log(f"图片格式  : {exts}")
        ci = next((e for e in infos if e.filename.lower().endswith("comicinfo.xml")), None)
        log(f"ComicInfo : {'有 -> ' + ci.filename if ci else '无'}")
        bad = [e.filename for e in infos if "\\" in e.filename]
        log(f"反斜杠条目: {len(bad)}" + ("  <-- 有兼容性风险" if bad else "  (正常)"))


def cmd_list(args):
    with open_src(args.cbz) as z:
        rows = [e for e in z.infolist() if not e.is_dir()]
        if args.images_only:
            rows = [e for e in rows if is_image(e.filename)]
        if args.match:
            rows = [e for e in rows if match_any(e.filename, args.match)]
        if args.sort == "natural":
            rows.sort(key=lambda e: natural_key(e.filename))
        for i, e in enumerate(rows):
            log(f"{i:>5}  {fmt_size(e.file_size):>8}  {e.filename}")
        log(f"合计 {len(rows)} 条")


def cmd_check(args):
    with open_src(args.cbz) as z:
        infos = z.infolist()
        imgs = [e for e in infos if not e.is_dir() and is_image(e.filename)]
        issues = []
        if not imgs:
            issues.append("包里没有图片")
        for e in infos:
            if "\\" in e.filename:
                issues.append(f"反斜杠分隔符: {e.filename}")
            if is_junk(e.filename):
                issues.append(f"垃圾文件: {e.filename}")
        names = [e.filename for e in infos if not e.is_dir()]
        dup = {n for n in names if names.count(n) > 1}
        for n in sorted(dup):
            issues.append(f"重复条目名: {n}")
        bad_pad = []
        for e in imgs:
            stem = PurePosixPath(e.filename).stem
            m = re.search(r"(\d+)$", stem)
            if m and len(m.group(1)) < 3 and not m.group(1).startswith("0"):
                bad_pad.append(e.filename)
        if bad_pad:
            issues.append(f"{len(bad_pad)} 个页面文件名未零填充，阅读顺序可能错乱，"
                          f"例如: {bad_pad[:3]}  (可用 rename --renumber 修复)")
        cover = [e.filename for e in infos if PurePosixPath(e.filename).name.lower().startswith("000_cover")]
        if not cover and imgs:
            first = min((e.filename for e in imgs), key=natural_key)
            issues.append(f"没有 000_cover.* 封面条目，阅读器会用第一页: {first}")
        if not any(e.filename.lower().endswith("comicinfo.xml") for e in infos):
            issues.append("缺少 ComicInfo.xml（元数据/卷号信息）")
        log(f"图片 {len(imgs)} 页 / 条目 {len(infos)} 条")
        if issues:
            log(f"发现 {len(issues)} 个问题:")
            for s in issues:
                log(f"  - {s}")
        else:
            log("未发现问题")


def cmd_extract(args):
    with open_src(args.cbz) as z:
        rows = [e for e in z.infolist() if not e.is_dir()]
        if args.match:
            rows = [e for e in rows if match_any(e.filename, args.match)]
        if args.pages:
            keep = set()
            for spec in args.pages:
                for part in spec.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "-" in part:
                        a, b = part.split("-", 1)
                        keep.update(range(int(a), int(b) + 1))
                    elif part.isdigit():
                        keep.add(int(part))
            imgs = [e for e in rows if is_image(e.filename)]
            imgs.sort(key=lambda e: natural_key(e.filename))
            rows = [e for e in rows if not is_image(e.filename)] + \
                   [e for i, e in enumerate(imgs) if i in keep]
        if not rows:
            sys.exit("[错误] 没有匹配的条目")
        root = Path(args.out).resolve()
        for e in rows:
            name = norm_entry_name(e.filename)
            target = (root / name).resolve()
            if not str(target).startswith(str(root)):
                log(f"[跳过] 越界路径: {e.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(e) as s, target.open("wb") as d:
                shutil.copyfileobj(s, d, 256 * 1024)
        log(f"[解包] {len(rows)} 个条目 -> {root}")


def cmd_add(args):
    out = resolve_out(args.cbz, args)
    new_files = [(Path(p).name, p) for p in args.files]
    with open_src(args.cbz) as z:
        infos = z.infolist()
        existing = {e.filename for e in infos}
        plan = []
        for base, p in new_files:
            name = args.name if (args.name and len(new_files) == 1) else base
            if args.into:
                name = args.into.replace("\\", "/").strip("/") + "/" + PurePosixPath(name).name
            name = norm_entry_name(name)
            if name in existing:
                stem, suf = os.path.splitext(name)
                i = 2
                while f"{stem}_{i}{suf}" in existing:
                    i += 1
                log(f"[提示] {name} 已存在，改用 {stem}_{i}{suf}")
                name = f"{stem}_{i}{suf}"
            existing.add(name)
            plan.append((name, p))
        pos = args.at
        if pos is None:
            imgs = [i for i, e in enumerate(infos) if not e.is_dir() and is_image(e.filename)]
            pos = (imgs[-1] + 1) if imgs else len(infos)
        if pos < 0:
            pos = max(0, len(infos) + pos)
        log(f"[插入位置] 索引 {pos}（0 起，共 {len(infos)} 条）")

        def writer(zout):
            with zipfile.ZipFile(args.cbz) as zin:
                for i, e in enumerate(zin.infolist()):
                    if i == pos:
                        for name, p in plan:
                            add_file(zout, name, p)
                    copy_entry(zin, e, zout)
                if pos >= len(zin.infolist()):
                    for name, p in plan:
                        add_file(zout, name, p)

    # 源文件句柄已关闭，原地改写时 Windows 才允许替换
    build_zip(args.cbz, out, writer)
    for name, p in plan:
        log(f"[加入] {name}  <- {p}")


def cmd_remove(args):
    if not args.match and not args.regex:
        sys.exit("[错误] 需要给出匹配模式，或 --regex")
    pat = re.compile(args.regex) if args.regex else None
    with open_src(args.cbz) as z:
        infos = z.infolist()
        hit = [e for e in infos if not e.is_dir() and
               (match_any(e.filename, args.match) or (pat and pat.search(e.filename)))]
        if not hit:
            log("没有匹配到任何条目，未做修改")
            return
        log(f"匹配 {len(hit)} 条:")
        for e in hit:
            log(f"  - {e.filename}")
        if args.dry_run:
            log("[dry-run] 未写出文件")
            return
        drop = {e.filename for e in hit}
        if len(drop) != len(hit):
            log("[提示] 存在同名条目，将全部删除")
        out = resolve_out(args.cbz, args)

        def writer(zout):
            with zipfile.ZipFile(args.cbz) as zin:
                for e in zin.infolist():
                    if e.filename in drop:
                        continue
                    copy_entry(zin, e, zout)

    build_zip(args.cbz, out, writer)


def cmd_replace(args):
    pairs = []
    if args.entry and args.newfile:
        pairs.append((args.entry, args.newfile))
    if args.pair:
        for spec in args.pair:
            if "=" not in spec:
                sys.exit(f"[错误] --pair 需要 OLD=NEW 形式: {spec}")
            a, b = spec.split("=", 1)
            pairs.append((a, b))
    if not pairs:
        sys.exit("[错误] 用法: replace CBZ 条目名 新文件  或  replace CBZ --pair OLD=NEW")
    out = resolve_out(args.cbz, args)
    mapping = {norm_entry_name(a): b for a, b in pairs}
    with open_src(args.cbz) as z:
        names = {e.filename for e in z.infolist()}
        for k in mapping:
            if k not in names:
                sys.exit(f"[错误] 包里没有条目: {k}")
        done = set()

        def writer(zout):
            with zipfile.ZipFile(args.cbz) as zin:
                for e in zin.infolist():
                    if e.filename in mapping:
                        add_file(zout, e.filename, mapping[e.filename],
                                 compress=None if is_image(e.filename) else zipfile.ZIP_DEFLATED)
                        done.add(e.filename)
                    else:
                        copy_entry(zin, e, zout)

    build_zip(args.cbz, out, writer)
    for k in done:
        log(f"[替换] {k}  <- {mapping[k]}")


def cmd_rename(args):
    rules = [r.split("=", 1) for r in (args.subst or [])]
    with open_src(args.cbz) as z:
        infos = z.infolist()
        imgs = [e for e in infos if not e.is_dir() and is_image(e.filename)]
        others = [e for e in infos if e.is_dir() or not is_image(e.filename)]
        order = list(imgs)
        if args.sort_by == "natural":
            order.sort(key=lambda e: natural_key(e.filename))

        # 计算新名字（按目录各自计数，避免同名覆盖）
        new_names = {}
        if args.renumber:
            counters = {}
            for e in order:
                d = "" if args.flat else str(PurePosixPath(e.filename).parent)
                if d == ".":
                    d = ""
                i = counters.get(d, args.start)
                counters[d] = i + 1
                base = f"{args.prefix}{i:0{args.digits}d}{PurePosixPath(e.filename).suffix.lower()}"
                new_names[e.filename] = f"{d}/{base}" if d else base
        for e in order:
            name = new_names.get(e.filename, e.filename)
            if args.flat:
                name = PurePosixPath(name).name
            for pat, rep in rules:
                name = re.sub(pat, rep, name)
            if args.prefix and not args.renumber:
                d = PurePosixPath(name).parent
                base = args.prefix + PurePosixPath(name).name
                name = str(d / base) if str(d) != "." else base
            new_names[e.filename] = name

        changed = {k: v for k, v in new_names.items() if k != v}
        if not changed:
            log("没有条目需要改名")
            return
        dup = sorted({v for v, c in Counter(new_names.values()).items() if c > 1})
        if dup:
            sys.exit(f"[错误] 改名后出现重名，已中止: {dup[:3]}（请调整 --prefix/--digits/--subst）")
        clash = sorted(set(new_names.values()) & {e.filename for e in others})
        if clash:
            sys.exit(f"[错误] 与包内其他条目重名，已中止: {clash[:3]}")
        log(f"将重命名 {len(changed)} 个条目，例如:")
        for k in list(changed)[:6]:
            log(f"  {k}  ->  {changed[k]}")
        if args.dry_run:
            log("[dry-run] 未写出文件")
            return
        out = resolve_out(args.cbz, args)

        def writer(zout):
            with zipfile.ZipFile(args.cbz) as zin:
                for e in zin.infolist():
                    copy_entry(zin, e, zout, new_names.get(e.filename, e.filename))

    build_zip(args.cbz, out, writer)


def cmd_sort(args):
    """按 order-file 指定的顺序重排包内条目（可顺带重新编号）。"""
    lines = [l.strip() for l in Path(args.order_file).read_text(encoding="utf-8-sig").splitlines()]
    wanted = [norm_entry_name(l) for l in lines if l and not l.startswith("#")]
    with open_src(args.cbz) as z:
        infos = z.infolist()
        imgs = [e for e in infos if not e.is_dir() and is_image(e.filename)]
        names = {e.filename for e in imgs}
        missing = [w for w in wanted if w not in names]
        if missing:
            sys.exit(f"[错误] order-file 里有 {len(missing)} 个条目不在包中，例如: {missing[:3]}")
        rest = [e.filename for e in sorted(imgs, key=lambda e: natural_key(e.filename))
                if e.filename not in set(wanted)]
        seq = wanted + rest
        log(f"新顺序: 指定 {len(wanted)} 条 + 其余 {len(rest)} 条 = {len(seq)} 页")
        renum = {}
        if args.renumber:
            counters = {}
            for n in seq:
                d = str(PurePosixPath(n).parent)
                d = "" if d == "." else d
                i = counters.get(d, args.start)
                counters[d] = i + 1
                new = f"{i:0{args.digits}d}{PurePosixPath(n).suffix.lower()}"
                renum[n] = f"{d}/{new}" if d else new
        out = resolve_out(args.cbz, args)
        order_map = {}
        for i, n in enumerate(seq):
            order_map[n] = renum.get(n, n)
        dup = {v for v, c in Counter(order_map.values()).items() if c > 1}
        if dup:
            sys.exit(f"[错误] 重编号后出现重名，已中止: {sorted(dup)[:3]}")

        def writer(zout):
            with zipfile.ZipFile(args.cbz) as zin:
                head = [e for e in zin.infolist() if e.is_dir() or not is_image(e.filename)]
                for e in head:
                    copy_entry(zin, e, zout)
                by_name = {e.filename: e for e in zin.infolist()}
                for n in seq:
                    copy_entry(zin, by_name[n], zout, order_map[n])

    build_zip(args.cbz, out, writer)


CI_FIELDS = ["Series", "Title", "Number", "Volume", "Count", "Summary", "Year",
             "Month", "Day", "Writer", "Penciller", "Genre", "LanguageISO",
             "Publisher", "PageCount", "Notes", "Manga"]


def cmd_meta(args):
    target = args.comicinfo or "ComicInfo.xml"
    with open_src(args.cbz) as z:
        names = [e.filename for e in z.infolist()]
        ci_name = next((n for n in names if n.lower() == target.lower() or
                        n.lower().endswith("/" + target.lower())), None)
        if ci_name is None and not args.create and (args.set or args.unset):
            log(f"[提示] 包里没有 {target}，自动创建")
            args.create = True
        if ci_name:
            with z.open(ci_name) as f:
                raw = f.read()
            root = ET.fromstring(raw.decode("utf-8-sig"))
        else:
            root = ET.Element("ComicInfo")
            for k, v in (("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance"),
                         ("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")):
                root.set(k, v)
    if args.show or not (args.set or args.unset):
        log(f"--- {ci_name or target} ({'包内' if ci_name else '不存在'}) ---")
        found = False
        for child in root:
            if (child.text or "").strip():
                log(f"  {child.tag:<12}= {child.text}")
                found = True
        if not found:
            log("  (空)")
        if not (args.set or args.unset):
            return
    for k, v in (s.split("=", 1) for s in (args.set or [])):
        el = root.find(k)
        if el is None:
            el = ET.SubElement(root, k)
        el.text = v
        log(f"[设置] {k} = {v}")
    for k in (args.unset or []):
        el = root.find(k)
        if el is not None:
            root.remove(el)
            log(f"[删除] {k}")
    order = {k: i for i, k in enumerate(CI_FIELDS)}
    children = list(root)
    children.sort(key=lambda c: (order.get(c.tag, 999), c.tag))
    for c in children:
        root.remove(c)
    for c in children:
        root.append(c)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    out = resolve_out(args.cbz, args)

    def writer(zout):
        with zipfile.ZipFile(args.cbz) as zin:
            for e in zin.infolist():
                if ci_name and e.filename == ci_name:
                    continue
                copy_entry(zin, e, zout)
            info = zipfile.ZipInfo(ci_name or target, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, body)

    build_zip(args.cbz, out, writer)


def cmd_repack(args):
    src = Path(args.dir)
    if not src.is_dir():
        sys.exit(f"[错误] 不是目录: {src}")
    out = Path(args.output) if args.output else src.with_suffix(".cbz")
    files = []
    for p in sorted(src.rglob("*"), key=lambda p: natural_key(str(p.relative_to(src)))):
        if p.is_file() and not is_junk(p.name):
            if args.images_only and p.suffix.lower() not in IMAGE_EXT:
                continue
            files.append(p)
    if not files:
        sys.exit("[错误] 目录里没有可打包的文件")
    if args.output and Path(args.output).exists() and not args.force:
        sys.exit(f"[错误] 输出已存在，加 --force 覆盖: {out}")
    if out.exists():
        if out.is_dir():
            sys.exit(f"[错误] 输出路径是目录: {out}")
        out.unlink()

    def writer(zout):
        for p in files:
            rel = PurePosixPath(p.relative_to(src).as_posix())
            name = str(rel)
            if args.prefix:
                name = str(PurePosixPath(args.prefix) / rel)
            add_file(zout, name, p)

    build_zip(None, out, writer)
    log(f"[打包] {len(files)} 个文件 -> {out}")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(
        prog="cbz_tool.py",
        description="编辑 .cbz 漫画包（增删页、替换、改名、重排、元数据、拆分打包）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_out(p):
        p.add_argument("-o", "--output", help="输出路径（默认 <名>.edited.cbz）")
        p.add_argument("--in-place", action="store_true",
                       help="原地改写（自动生成 .bak 备份）")

    p = sub.add_parser("info", help="概览：页数、章节目录、格式、元数据")
    p.add_argument("cbz")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", help="列出条目")
    p.add_argument("cbz")
    p.add_argument("--images-only", action="store_true")
    p.add_argument("--match", action="append", help="glob 过滤，可重复")
    p.add_argument("--sort", choices=["none", "natural"], default="none")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="体检：命名、页序、垃圾文件、元数据")
    p.add_argument("cbz")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("extract", help="解包（可按 glob 或页序）")
    p.add_argument("cbz")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--match", action="append", help="glob 过滤，可重复")
    p.add_argument("--pages", action="append", help="按页序，如 0-4,10")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("add", help="加入图片（可指定位置/章节目录）")
    p.add_argument("cbz")
    p.add_argument("files", nargs="+")
    p.add_argument("--at", type=int, help="插入位置索引（0 起）")
    p.add_argument("--name", help="仅单个文件时指定条目名")
    p.add_argument("--into", help='放进子目录，如 "001_第01话/"')
    add_out(p)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="删除条目")
    p.add_argument("cbz")
    p.add_argument("match", nargs="*", help="glob，可给多个")
    p.add_argument("--regex", help="正则匹配条目名")
    p.add_argument("--dry-run", action="store_true")
    add_out(p)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("replace", help="替换某个条目的内容（换图）")
    p.add_argument("cbz")
    p.add_argument("entry", nargs="?")
    p.add_argument("newfile", nargs="?")
    p.add_argument("--pair", action="append", help="OLD=NEW，可重复")
    add_out(p)
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("rename", help="批量改名 / 零填充重编号 / 正则替换")
    p.add_argument("cbz")
    p.add_argument("--renumber", action="store_true", help="按当前顺序重新编号")
    p.add_argument("--digits", type=int, default=4)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--prefix", default="")
    p.add_argument("--flat", action="store_true", help="去掉目录层级")
    p.add_argument("--subst", action="append", help="正则替换 OLD=NEW")
    p.add_argument("--sort-by", choices=["stored", "natural"], default="natural")
    p.add_argument("--dry-run", action="store_true")
    add_out(p)
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("sort", help="按 order-file 指定顺序重排页面")
    p.add_argument("cbz")
    p.add_argument("--order-file", required=True)
    p.add_argument("--renumber", action="store_true")
    p.add_argument("--digits", type=int, default=4)
    p.add_argument("--start", type=int, default=0)
    add_out(p)
    p.set_defaults(func=cmd_sort)

    p = sub.add_parser("meta", help="读写 ComicInfo.xml")
    p.add_argument("cbz")
    p.add_argument("--show", action="store_true")
    p.add_argument("--set", action="append", help="KEY=VALUE，可重复")
    p.add_argument("--unset", action="append", help="字段名，可重复")
    p.add_argument("--comicinfo", help="自定义条目名，默认 ComicInfo.xml")
    p.add_argument("--create", action="store_true", help="不存在时创建")
    add_out(p)
    p.set_defaults(func=cmd_meta)

    p = sub.add_parser("repack", help="从目录重新打包成 cbz")
    p.add_argument("dir")
    p.add_argument("-o", "--output")
    p.add_argument("--images-only", action="store_true")
    p.add_argument("--prefix", help="统一加一层目录前缀")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_repack)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except zipfile.BadZipFile as e:
        sys.exit(f"[错误] zip 读取失败: {e}")
