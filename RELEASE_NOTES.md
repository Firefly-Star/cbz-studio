# cbz 工具箱 v1.0.0

边看边改 `.cbz`（漫画包，本质是改了扩展名的 zip）的工具。这个版本包含 Windows 桌面版与 Android 版。

## 下载

| 文件 | 平台 | 大小 | 说明 |
|---|---|---|---|
| `cbz-editor.exe` | Windows 10/11 x64 | 17.9 MB | 单文件 GUI，双击即用，**不需要装 Python**（自带 Tk 与 Pillow） |
| `cbz-studio.apk` | Android 7.0+（minSdk 24） | 66 KB | 原生 Java 应用，debug 签名，可直接侧载 |

SHA-256 校验和：

```
D2B4AD403B2239CC652161470438B3B93D7177157EB557716437FE89E4F27DD2  cbz-editor.exe
36290574B884D8DB0DB3498CB14952D406C468161DFA4A03A56B5F0709B1AF6E  cbz-studio.apk
```

## 功能

**Windows 桌面版**（[截图](cbz_editor_screenshot.png)）

- 左边章节目录树、中间当前页、底部状态栏，边翻页边改
- 删除页 / 撤销、插入图片（可多选）、旋转 ±90°、上下移动、改章节目录、设为封面
- 日漫右开自动识别（读 `ComicInfo.xml` 的 `Manga` 字段）、双页跨页、缩放适应
- 改动先在内存里打标记（`✗` 待删、`＋` 新插入），`Ctrl+S` 才落盘；默认另存为
  `<原名>.edited.cbz`，**不覆盖原文件**
- 可选「保存时按目录重编号」；`ComicInfo.xml` 的 `PageCount` 自动更新
- 快捷键：`←`/`→` 翻页 · `Delete` 删 · `Insert` 加 · `R` 旋转 · `T` 改章节 · `D` 双页 · `F` 适应 · `Ctrl+Z` 撤销

**Android 版**

- 滑动 / 点按左右 1/3 / 音量键翻页，工具条按钮与桌面版一一对应
- 打开与导出都走系统文件选择器（SAF），**不需要任何权限**
- 按章节目录跳「上一话 / 下一话」

## 安装

**Windows**：下载 `cbz-editor.exe` 直接双击。首次运行 Windows 可能弹「已保护你的电脑」
（未做代码签名的常见提示），点「更多信息 → 仍要运行」。

**Android**：把 `cbz-studio.apk` 传到手机 → 点开 → 按提示允许「安装未知来源应用」。
应用签名证书 SHA-256：`521e727dd52d23bbb0a31ec89670439fb4b96ded7a9430954d62f1d6dd329ed8`

## 已知限制（重要）

- **Android 版没有在真机上跑过**：开发机上既没有设备也没有模拟器（`adb devices` 为空）。
  逻辑层 `CbzDoc` 在桌面 JVM 上通过 20 项断言的完整测试（增删/撤销/旋转/插入/改章节/保存 + 产物复核），
  产出的 cbz 也用独立实现的 Python 版工具交叉复核为「未发现问题」；APK 本身通过
  dex 合法性、清单含 LAUNCHER、5 种密度图标、`zipalign -c -p 4`、v2+v3 签名、
  `resources.arsc` 未压缩等静态校验。但**界面布局、手势、SAF 交互没有真机验证**，
  发现问题请开 issue。
- Android `targetSdk` 是 34：Android 15 对 `targetSdk >= 35` 强制 edge-to-edge，
  会把顶部状态条压在系统状态栏下，而没有真机无法确认视觉效果，所以先保守取 34。
- APK 是**调试签名**，适合自己侧载，不适合上架应用商店。
- Windows 单文件版启动时会把自身解压到系统临时目录（约 1–2 秒）。若系统临时目录不可写，
  会报 `Could not create temporary directory!` —— 受限环境才会遇到，正常双击不受影响；
  真遇到可用仓库里 `-Onedir` 构建的目录版。
- 保存时会整包重写：736 MB 的包写一次约需 1.5 GB 临时空间。
- 只有旋转过的页会重新编码（JPEG q95 / 4:4:4），其余页原样流式拷贝，画质无损。

## 从源码跑

仓库里还有命令行版 `cbz_tool.py`（`info` / `check` / `extract` / `add` / `remove` / `replace` /
`rename` / `sort` / `meta` / `repack`）和完整的构建脚本：

```powershell
python cbz_editor.py "某漫画.cbz"                  # 直接用 Python 跑 GUI
python cbz_editor.py --selftest "某漫画.cbz"       # 无界面自检（14 项断言）
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Onedir
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -JvmTest
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
