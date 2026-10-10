#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 d8 产出的 classes.dex 塞进 aapt2 生成的 base.apk，产出 unsigned.apk。

用 Python 而不是 `jar uf`，是为了完全控制压缩方式：
targetSdk 30+ 要求 resources.arsc 必须**不压缩**且 4 字节对齐，否则安装时会报
INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION。这里会顺便检查并报错。
"""
import sys
import zipfile
from pathlib import Path

STORED, DEFLATED = zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED

# 控制台是 GBK 时，非 GBK 字符（✓ 之类）会让 print 抛 UnicodeEncodeError 并直接退出 1，
# 所以这里统一放宽成替换而不是报错。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) != 4:
        print("用法: pack_apk.py <base.apk> <classes.dex> <unsigned.apk>")
        return 2
    base, dex, out = (Path(p) for p in sys.argv[1:4])
    for p in (base, dex):
        if not p.is_file():
            print(f"[错误] 找不到 {p}")
            return 1

    with zipfile.ZipFile(base) as zin, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
        arsc_ok = None
        for info in zin.infolist():
            data = zin.read(info.filename)
            ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            ni.compress_type = info.compress_type
            ni.external_attr = info.external_attr
            zout.writestr(ni, data)
            if info.filename == "resources.arsc":
                arsc_ok = info.compress_type == STORED
        # dex 不压缩（和 AGP 对 API 27+ 的做法一致，启动更快）
        ni = zipfile.ZipInfo("classes.dex", date_time=(2009, 1, 1, 0, 0, 0))
        ni.compress_type = STORED
        zout.writestr(ni, dex.read_bytes())

    if arsc_ok is False:
        print("[错误] resources.arsc 是压缩存储的，targetSdk 30+ 装不上。"
              "请检查 aapt2 参数。")
        return 1

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        print(f"[打包] {out.name}: {len(names)} 个条目 "
              f"({sum(1 for n in names if n.endswith('.dex'))} 个 dex, "
              f"resources.arsc {'未压缩 ✓' if arsc_ok else '缺失'})")
        bad = z.testzip()
        if bad:
            print(f"[错误] zip 校验失败: {bad}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
