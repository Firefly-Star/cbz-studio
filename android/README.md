# cbz 编辑器 · Android 版

原生 Java 应用，把桌面版的核心功能搬到手机上：**一边翻着看，一边删页/加页/旋转/重排**，
最后导出新的 `.cbz`。

## 为什么不用 Gradle

这台机器上已经有完整的 Android SDK 与 JDK 17，但没有 Android Studio / Gradle 工程。
本项目刻意做到 **零第三方依赖、零 AndroidX**：

- UI 全部用框架自带的 widget 用代码搭建（没有 XML 布局），所以 `aapt2 link` 之后不需要任何 AAR 依赖解析；
- 打包直接用 SDK 自带的 `aapt2` / `d8` / `zipalign` / `apksigner`，**全程离线**，不需要联网拉 AGP 或 AndroidX；
- 结果：APK 只有 **65 KB**，构建一次约 5 秒。

## 构建

```powershell
# 方式一：双击
android\tools\build.cmd

# 方式二：命令行（PowerShell 默认禁止跑脚本，所以要 Bypass）
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1
powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -JvmTest   # 先跑逻辑自检
```

产物：

- `android\build\cbz-studio-debug.apk` —— 构建结果
- `dist\cbz-studio.apk` —— 同一个文件的发布副本

签名用的是首次构建时由 `keytool` 自动生成的 `android\tools\debug.keystore`（口令 `android`，
别名 `cbzdebug`），已加入 `.gitignore`。这是**调试签名**：适合自己侧载安装，
不适合上架应用商店。

参数可覆盖：`-Sdk`、`-BuildTools`、`-Platform`（默认 `C:\Users\Summer\Android\Sdk` / `35.0.0` / `android-35`）。

## 安装到手机

1. 把 `dist\cbz-studio.apk` 传到手机（数据线 / 微信文件传输 / 网盘都行）；
2. 手机上点开这个 APK，按提示允许「安装未知来源应用」；
3. 装完后打开应用，点「打开」选一个 `.cbz` 文件。

应用不需要任何权限：选文件、导出文件都走系统的文件选择器（SAF）。

## targetSdk 为什么是 34 而不是 35

Android 15（API 35）对 `targetSdk >= 35` 的应用**强制 edge-to-edge**，顶部内容默认会被状态栏压住。
这个应用没有真机可验证视觉效果，所以选择 `targetSdk 34` 避开这个行为，同时在根布局上加了
`setFitsSystemWindows(true)` 作为保险。`compileSdk` 仍然是 35（`--Platform android-35`）。
等你在真机上确认布局没问题，把 `build.ps1` 里的 `--target-sdk-version` 改成 `35` 即可。

## 代码结构

```
AndroidManifest.xml            清单（minSdk 24 / targetSdk 34，无 uses-permission）
res/values/strings.xml         应用名
res/mipmap-*/ic_launcher.png   图标（mdpi~xxxhdpi 五种密度）
src/com/cbzstudio/
  CbzDoc.java                  文档模型：页列表、增删、旋转、重排、改章节目录、保存为 zip
  MainActivity.java            UI：手势翻页、工具条、SAF 打开/导出
tools/
  build.ps1 / build.cmd        打包脚本
  pack_apk.py                  把 classes.dex 塞进 aapt2 产出的 APK，并校验 resources.arsc 未压缩
  jvmtest/JvmTest.java         桌面 JVM 自检
```

关键设计：**`CbzDoc.java` 只用 `java.*`，不 import 任何 `android.*`**。
旋转重编码通过 `CbzDoc.Transcoder` 接口注入 —— Android 端用 `Bitmap`+`Matrix` 实现，
桌面测试用 `ImageIO`+`AffineTransform` 实现。因此同一份核心逻辑可以在电脑上跑完整测试，
不用等真机。`MainActivity` 里 `AndroidTranscoder` 就是这么注入的。

## 功能与操作

| 操作 | 方式 |
|---|---|
| 翻页 | 左右滑动；点屏幕左/右 1/3；音量键；工具条 ◀ ▶ |
| 跳话 | 工具条「上一话 / 下一话」（按章节目录跳） |
| 删除当前页 | 工具条「删除」，可「撤销」 |
| 旋转 / 上移 / 下移 | 工具条对应按钮，可撤销 |
| 插入图片 | 工具条「插页」（可多选，插在当前页之后，并自动归入当前章节目录） |
| 改章节目录 | 工具条「章节」，填 `001_第01话` 之类，留空回根目录 |
| 右开开关 | 工具条「右开」（默认从 ComicInfo.xml 的 `Manga` 字段识别） |
| 隐藏工具条 | 点屏幕中间 |
| 导出 | 工具条「保存」→ 系统文件选择器 → 存成 `<原名>.edited.cbz`（按目录重编号、更新 PageCount） |

所有改动先存在内存里，导出时才写文件；返回键在未保存时会拦一下。

## 已验证 / 未验证

已验证（静态 + 桌面逻辑）：

- `tools\jvmtest` 的 20 项断言：删除、撤销、旋转标记、插入、改章节目录、上移、保存，
  以及产物复核（页数、无重名、无反斜杠、删除已落盘、旋转后宽高互换、插入页落在
  `099_新章节/0000.jpg`、`ComicInfo.xml` 的 `PageCount` 已更新）。
- Java 产出的 cbz 再用 Python 版 `cbz_tool.py` 独立复核：「未发现问题」。
- APK：`classes.dex` 魔数/长度合法且含 `MainActivity`、`CbzDoc`、`CbzDoc$Transcoder`；
  二进制清单含 `com.cbzstudio` / `MainActivity` / `LAUNCHER`；5 种密度图标都在；
  `zipalign -c -p 4` 通过；`apksigner verify` 显示 v2 + v3 为 true；
  `resources.arsc` 与 `classes.dex` 均为未压缩存储（targetSdk 30+ 的安装硬性要求）。

**未验证**：UI 的运行时表现。本机 `adb devices` 为空，SDK 里也没装 emulator，
所以没法启动应用真跑一遍。首次安装后如果哪里不对（布局、手势、SAF 交互），
告诉我具体现象我来修。

## 想加东西的话

`CbzDoc` 是纯逻辑层，加功能（比如「按页码范围拆分成多卷」「把整话导出成单独文件」）
只要加方法 + 在 `JvmTest` 里补断言即可，改完跑一遍 `build.ps1 -JvmTest` 就有保障。
