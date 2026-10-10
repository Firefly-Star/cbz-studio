# cbz 编辑器 Android 打包脚本
#
# 不使用 Gradle：直接用 Android SDK 自带的 aapt2 / d8 / zipalign / apksigner，
# 加上 JDK 的 javac / jar / keytool 出可安装的 APK。全部本地离线完成。
#
# 用法（PowerShell 默认禁止运行脚本，所以要带 -ExecutionPolicy Bypass）:
#   powershell -ExecutionPolicy Bypass -File android\tools\build.ps1
#   powershell -ExecutionPolicy Bypass -File android\tools\build.ps1 -JvmTest
# 或者直接双击 android\tools\build.cmd

param(
    [switch]$JvmTest,
    [string]$Sdk = "C:\Users\Summer\Android\Sdk",
    [string]$BuildTools = "35.0.0",
    [string]$Platform = "android-35"
)

# 必须是 Continue 而不是 Stop：PowerShell 5.1 会把子进程写到 stderr 的正常输出
# （javac 的过时 API 提示、keytool / apksigner 的信息）当成致命错误。
# 原生命令统一用 Run 包装，只看退出码。
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# 子进程 python 也用 UTF-8 输出：否则 ✓ 这类非 GBK 字符会让 print 抛异常、退出码 1
$env:PYTHONIOENCODING = 'utf-8'

$BT   = Join-Path $Sdk "build-tools\$BuildTools"
$PLAT = Join-Path $Sdk "platforms\$Platform\android.jar"
$JDK  = $env:JAVA_HOME
$ROOT = Split-Path -Parent $PSScriptRoot                    # android/
$WS   = Split-Path -Parent $ROOT                            # 仓库根
$OUT  = Join-Path $ROOT "build"
$GEN  = Join-Path $OUT "gen"
$CLS  = Join-Path $OUT "classes"
$DEX  = Join-Path $OUT "dex"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Need($path, $what) { if (-not (Test-Path $path)) { throw "缺少 $what : $path" } }

# 跑原生命令：stderr 合并进正常输出（否则 PowerShell 会染成红色错误块），按退出码判断成败
function Run {
    param([Parameter(ValueFromRemainingArguments = $true)]$Argv)
    $exe = $Argv[0]
    $rest = @()
    if ($Argv.Count -gt 1) { $rest = @($Argv[1..($Argv.Count - 1)]) }
    & $exe @rest 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $exe) 退出码 $LASTEXITCODE" }
}

# 参数用显式数组传，避免 PowerShell 把 --xxx 当成自己的参数名
function RunArr {
    param([string]$Exe, [string[]]$Argv)
    & $Exe @Argv 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $Exe) 退出码 $LASTEXITCODE" }
}

Need (Join-Path $BT "aapt2.exe") "aapt2"
Need (Join-Path $BT "d8.bat") "d8"
Need (Join-Path $BT "zipalign.exe") "zipalign"
Need (Join-Path $BT "apksigner.bat") "apksigner"
Need $PLAT "android.jar ($Platform)"
Need (Join-Path $JDK "bin\javac.exe") "javac (JAVA_HOME=$JDK)"

$resDir  = Join-Path $ROOT "res"
$manifest = Join-Path $ROOT "AndroidManifest.xml"
$resZip  = Join-Path $OUT "res.zip"
$baseApk = Join-Path $OUT "base.apk"
$clsJar  = Join-Path $OUT "classes.jar"
$unsigned = Join-Path $OUT "unsigned.apk"
$aligned = Join-Path $OUT "aligned.apk"
$final   = Join-Path $OUT "cbz-studio-debug.apk"

foreach ($d in @($GEN, $CLS, $DEX)) {
    Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}

# 0) 桌面 JVM 逻辑自检（可选）：先确认 CbzDoc 真的能增删改存
if ($JvmTest) {
    Step "0/6 桌面 JVM 自检 CbzDoc"
    $jcls = Join-Path $OUT "jvmtest\classes"
    New-Item -ItemType Directory -Path $jcls -Force | Out-Null
    RunArr "$JDK\bin\javac.exe" @("-encoding", "UTF-8", "-d", $jcls,
        (Join-Path $ROOT "src\com\cbzstudio\CbzDoc.java"),
        (Join-Path $ROOT "tools\jvmtest\JvmTest.java"))
    RunArr "$JDK\bin\java.exe" @("-Dfile.encoding=UTF-8", "-cp", $jcls,
        "com.cbzstudio.JvmTest", (Join-Path $WS "manga\sample_2ch.cbz"),
        (Join-Path $OUT "jvmtest"))
}

# 1) 资源编译
Step "1/6 aapt2 compile（资源）"
RunArr "$BT\aapt2.exe" @("compile", "--dir", $resDir, "-o", $resZip)

# 2) 链接资源 + 清单 -> base.apk 与 R.java
Step "2/6 aapt2 link（生成 base.apk + R.java）"
RunArr "$BT\aapt2.exe" @("link", "-o", $baseApk, "-I", $PLAT,
    "--manifest", $manifest, "-R", $resZip, "--java", $GEN,
    "--min-sdk-version", "24", "--target-sdk-version", "34",
    "--version-code", "1", "--version-name", "1.0", "--auto-add-overlay")

# 3) javac：Android 需要 Java 8 字节码
Step "3/6 javac（Java 8 字节码，bootclasspath = android.jar）"
$srcs = @(Get-ChildItem (Join-Path $ROOT "src"), $GEN -Recurse -Filter *.java |
          ForEach-Object { $_.FullName })
Write-Host ("源文件 {0} 个" -f $srcs.Count)
RunArr "$JDK\bin\javac.exe" (@("-encoding", "UTF-8", "-nowarn", "-source", "8", "-target", "8",
    "-bootclasspath", $PLAT, "-classpath", $PLAT, "-d", $CLS) + $srcs)

# 4) d8：class -> dex
Step "4/6 d8（class -> classes.dex）"
RunArr "$JDK\bin\jar.exe" @("cf", $clsJar, "-C", $CLS, ".")
RunArr "$BT\d8.bat" @("--lib", $PLAT, "--min-api", "24", "--release",
    "--output", $DEX, $clsJar)

# 5) 组装 APK（Python 控制压缩方式，保证 resources.arsc 未压缩）
Step "5/6 组装 APK（塞入 classes.dex）"
RunArr "python" @((Join-Path $ROOT "tools\pack_apk.py"), $baseApk,
    (Join-Path $DEX "classes.dex"), $unsigned)

# 6) zipalign + 签名
Step "6/6 zipalign + apksigner"
RunArr "$BT\zipalign.exe" @("-f", "-p", "4", $unsigned, $aligned)

$ks = Join-Path $ROOT "tools\debug.keystore"
if (-not (Test-Path $ks)) {
    Write-Host "生成调试签名用 keystore: $ks"
    RunArr "$JDK\bin\keytool.exe" @("-genkeypair", "-keystore", $ks,
        "-storepass", "android", "-keypass", "android", "-alias", "cbzdebug",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=cbz studio, OU=dev, O=cbz, L=NA, ST=NA, C=CN")
}

RunArr "$BT\apksigner.bat" @("sign", "--ks", $ks, "--ks-pass", "pass:android",
    "--key-pass", "pass:android", "--ks-key-alias", "cbzdebug",
    # minSdk 24（Android 7.0）只认 v2+，v1 的 JAR 签名纯属多余的 META-INF 垃圾
    "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
    "--v3-signing-enabled", "true",
    "--out", $final, $aligned)
RunArr "$BT\apksigner.bat" @("verify", "--print-certs", $final)

Write-Host "`n--- APK 信息 ---" -ForegroundColor Green
& "$BT\aapt.exe" dump badging $final 2>&1 | Select-String -Pattern `
    "package:|sdkVersion|targetSdkVersion|application-label|application-icon|launchable-activity"

$dist = Join-Path $WS "dist"
New-Item -ItemType Directory -Path $dist -Force | Out-Null
Copy-Item $final (Join-Path $dist "cbz-studio.apk") -Force

$size = [math]::Round((Get-Item $final).Length / 1MB, 2)
Write-Host "`n完成: $final ($size MB)" -ForegroundColor Green
Write-Host "已复制到: $(Join-Path $dist 'cbz-studio.apk')" -ForegroundColor Green
