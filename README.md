# cbz 工具箱

边看边改 `.cbz`（漫画包，本质是改了扩展名的 zip）的三件套：桌面图形界面、命令行工具、Android 应用。

| 产物 | 文件 | 说明 |
|---|---|---|
| 桌面 GUI | `cbz_editor.py` | Tkinter 阅读/编辑器：边翻页边增删改 |
| 命令行 | `cbz_tool.py` | 纯标准库，适合脚本/批量处理 |
| Android | `android/` | 原生 Java 应用，`dist/cbz-studio.apk` |
| Windows exe | `dist/cbz-editor.exe`、`dist/cbz-tool.exe` | PyInstaller 打包，双击即用 |

---

## 桌面 GUI（cbz_editor.py）

```powershell
python cbz_editor.py                      # 启动后从「打开」选文件
python cbz_editor.py "manga\某漫画.cbz"    # 直接打开
python cbz_editor.py --selftest "x.cbz"   # 无界面自检
```

左边章节目录树、中间当前页、底部状态栏。改动先在内存打标记（`✗` 待删、`＋` 新插入），**Ctrl+S** 才落盘，**Ctrl+Z** 随时撤销。

| 操作 | 快捷键 |
|---|---|
| 翻页 | `←`/`→`（日漫右开时自动反转）、滚轮、空格 |
| 删除当前页 | `Delete` |
| 插入图片 | `Insert`（可多选） |
| 旋转 / 上移 / 下移 | `R`、`Shift+R` / `Alt+↑`、`Alt+↓` |
| 改章节目录（可多选批量） | `T` |
| 设为封面 | `C` |
| 跳话 / 跳页 / 首末页 | `PgUp`、`PgDn` / `G` / `Home`、`End` |
| 双页 / 右开 / 适应 | `D` / `M` / `F` |
| 保存 / 另存为 / 打开 | `Ctrl+S` / `Ctrl+Shift+S` / `Ctrl+O` |

保存默认另存为 `<名字>.edited.cbz`（不覆盖原文件），勾选「保存时重编号」会按章节目录零填充重编号；`Ctrl+S` 到已保存过的路径时才算原地覆盖，并自动留 `.bak`。

## 命令行（cbz_tool.py）

```powershell
python cbz_tool.py info    manga\sample.cbz          # 页数/章节目录/格式/元数据概览
python cbz_tool.py check   manga\sample.cbz          # 体检：命名、页序、垃圾文件、缺元数据
python cbz_tool.py extract manga\sample.cbz -o out --match "001_*"
python cbz_tool.py add     manga\sample.cbz new.jpg --into "001_第01话/" --at 20
python cbz_tool.py remove  manga\sample.cbz "*0035.jpg" --dry-run
python cbz_tool.py replace manga\sample.cbz "001_第01话/0003.jpg" fixed.jpg
python cbz_tool.py rename  manga\sample.cbz --renumber --digits 4
python cbz_tool.py sort    manga\sample.cbz --order-file order.txt --renumber
python cbz_tool.py meta    manga\sample.cbz --set Series=安達與島村 --set Volume=1
python cbz_tool.py repack  outdir -o new.cbz
```

所有会写盘的子命令默认输出到 `<名字>.edited.cbz`，加 `--in-place` 才原地改写并自动生成 `.bak`；
写临时文件后原子替换，中途失败不会留下半成品。

## Android（android/）

原生 Java（不依赖 AndroidX，**不用 Gradle**），功能对齐桌面版：翻页、删除/撤销、旋转、上移下移、
插入图片、改章节目录、右开开关、导出新 cbz。手势滑动翻页、点左右 1/3 翻页、音量键翻页。

```powershell
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1            # 构建
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -JvmTest   # 顺带跑逻辑自检
```

产物：`dist\cbz-studio.apk`（debug 签名，可直接侧载）。详见 [android/README.md](android/README.md)。

## Windows 单文件 exe

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1            # 构建
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Onedir    # 额外产出目录版兜底
```

脚本内部用 `uv` 建 `.venv` 并装 `pyinstaller` + `pillow`，再用
`pyinstaller --onefile --windowed` / `--onefile --console` 分别打包 GUI 与命令行，
最后自动跑一遍产物自检。也可以双击 `tools\build_exe.cmd`。

产物：`dist\cbz-editor.exe`（GUI，约 18 MB）、`dist\cbz-tool.exe`（命令行，约 8.8 MB）、
`dist\cbz-editor-dir\` 与 `dist\cbz-tool-dir\`（`-Onedir` 时的目录版）。
单文件版自带 Python 运行时与 Tk/Pillow，目标机器**不需要装 Python**，双击即用。

> **临时目录注意**：onefile 启动时会把自身解压到系统临时目录（`%TMP%`/`%TEMP%`）。
> 如果那个目录不可写（受限会话/沙箱里会遇到），会报
> `Could not create temporary directory!` 并直接退出 —— 这不是打包缺陷，
> 正常双击运行不受影响，也可以在启动前把 `TMP`/`TEMP` 指到可写目录，
> 或直接改用 `-Onedir` 的目录版（完全不落地临时文件）。
> 注意**不要**用 `--runtime-tmpdir .` 来绕：实测 GUI 版会因此
> `ImportError: DLL load failed while importing _tkinter`（`_MEIPASS` 变成相对路径后找不到 tcl DLL）。

## 目录结构

```
cbz_editor.py            桌面 GUI
cbz_tool.py              命令行工具
tools/
  build_exe.ps1          PyInstaller 打包 Windows exe
  build_exe.cmd          双击打包
android/
  AndroidManifest.xml
  src/com/cbzstudio/
    CbzDoc.java          纯 Java 文档模型（不引用 android.*，可在桌面 JVM 直接测）
    MainActivity.java    Android UI（框架 widget 代码构建，零依赖）
  res/values/strings.xml
  res/mipmap-*/          图标（5 种密度）
  tools/
    build.ps1            手动打包：aapt2 + javac + d8 + zipalign + apksigner
    build.cmd            双击构建
    pack_apk.py          把 classes.dex 塞进 APK，并确保 resources.arsc 未压缩
    jvmtest/JvmTest.java 桌面 JVM 逻辑自检（20 项断言）
dist/                    构建产物（已 gitignore，可随时重新生成）
manga/                   测试用漫画包（体积大，已 gitignore）
```

## 已验证的内容

- **桌面 GUI**：`--selftest` 14 项断言；真实窗口截图核对布局；真实 GUI 保存链路（删 1 + 转 1 + 插 1 → 76 页）产物通过 `zipfile.testzip()`，无重名，`ComicInfo.xml` 的 `PageCount` 自动更新。1651 页 / 70 话的大包：载入 0.09 s、翻页 15 ms/页。
- **命令行**：增删改查/重排/元数据/重打包均在真实包上跑通，产物无重名、无反斜杠条目。
- **Windows exe**：四个变体（onefile 与 onedir，GUI 与命令行）实测退出码全为 0 ——
  `cbz-tool.exe check` 报「未发现问题」，`cbz-editor.exe --selftest` 14 项断言全过（并截图确认窗口渲染正常），
  用冻结的 exe 解包出的图片经 Pillow 解码验证。
- **Android**：`CbzDoc` 桌面 JVM 自检 20 项全过，产物再用 Python 版工具独立复核（76 页、4 个章节目录、「未发现问题」）；APK 静态校验：dex 合法、清单含 LAUNCHER、5 种图标密度、`zipalign -c` 通过、v2+v3 签名验证通过、`resources.arsc` 未压缩。
- **未验证**：Android 端**没有实机/模拟器可跑**（adb 无设备、SDK 未装 emulator），UI 层未做运行时验证。

## 已知取舍

- 桌面 GUI 的改动都在内存打标记，保存时整包重写；736 MB 的包写一次约需 1.5 GB 临时空间。
- 只有旋转过的页会重新编码（JPEG q95 / 4:4:4），其余页原样流式拷贝，画质无损。
- Android 端打开 `content://` 的包会先复制到应用缓存目录；`file://` 直接就地打开。

## 许可证

MIT，见 [LICENSE](LICENSE)。
