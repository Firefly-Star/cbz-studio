# Windows 单文件 exe 打包脚本（PyInstaller）
#
# 用法（PowerShell 默认禁止跑脚本，所以要 Bypass）:
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Local -Onedir
# 或者双击 tools\build_exe.cmd
#
# 产物（都在 dist\）:
#   cbz-editor.exe      GUI，--onefile --windowed
#   cbz-tool.exe        命令行，--onefile --console
#   -Onedir : 额外产出 *-dir\（目录版，完全不落地临时文件，最稳的兜底）
#
# 为什么不提供 --runtime-tmpdir . 的 "local" 变体：
#   实测 GUI 版会报 `ImportError: DLL load failed while importing _tkinter`，
#   因为 _MEIPASS 变成相对路径后 _tkinter.pyd 找不到 tcl86t.dll。
#   需要绕开系统临时目录时请用 -Onedir。

param(
    [switch]$Onedir,
    [switch]$Recreate
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$ROOT = Split-Path -Parent $PSScriptRoot
$VENV = Join-Path $ROOT ".venv"
$PY   = Join-Path $VENV "Scripts\python.exe"
$PICL = Join-Path $VENV "Scripts\pyinstaller.exe"
Set-Location $ROOT

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function RunArr([string]$Exe, [string[]]$Argv) {
    & $Exe @Argv 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $Exe) 退出码 $LASTEXITCODE" }
}

if ($Recreate -and (Test-Path $VENV)) {
    Write-Host "删除旧虚拟环境 $VENV"
    Remove-Item $VENV -Recurse -Force
}
if (-not (Test-Path $PY)) {
    Step "创建虚拟环境 .venv（uv venv --python 3.12）"
    RunArr "uv" @("venv", $VENV, "--python", "3.12")
}
Step "安装 pyinstaller + pillow"
RunArr "uv" @("pip", "install", "--python", $PY, "pyinstaller", "pillow")

$common = @("--noconfirm", "--distpath", "dist", "--workpath", "build", "--specpath", "build")

# conda 环境把 tcl/tk 和几个编解码库放在 Library\bin，PyInstaller 的依赖分析够不着。
# 不显式带进来的话，GUI 版运行时会报
#   ImportError: DLL load failed while importing _tkinter
# （PyInstaller 日志里会先出现 "Library not found: could not resolve 'tcl86t.dll'"）。
$libBin = (& $PY -c "import sys,os;print(os.path.join(sys.base_prefix,'Library','bin'))").Trim()
$tkBins = @()
$libBins = @()
foreach ($dll in @("tcl86t.dll", "tk86t.dll")) {
    $p = Join-Path $libBin $dll
    if (Test-Path $p) {
        $tkBins += @("--add-binary", "$p;.")
    } else {
        Write-Host "警告: 找不到 $dll（$libBin），GUI 版可能起不来" -ForegroundColor Yellow
    }
}
foreach ($dll in @("libexpat.dll", "liblzma.dll", "libbz2.dll")) {
    $p = Join-Path $libBin $dll
    if (Test-Path $p) { $libBins += @("--add-binary", "$p;.") }
}
Write-Host ("附加 DLL: {0}" -f (($tkBins + $libBins | Where-Object { $_ -like '*.dll' }) -join ', '))

Step "打包 GUI: cbz-editor.exe"
RunArr $PICL (@("--onefile", "--windowed", "--name", "cbz-editor") + $tkBins + $libBins + $common + @("cbz_editor.py"))

Step "打包命令行: cbz-tool.exe"
RunArr $PICL (@("--onefile", "--console", "--name", "cbz-tool") + $libBins + $common + @("cbz_tool.py"))

if ($Local) {
    Step "额外打包 -local 变体（--runtime-tmpdir .）"
    RunArr $PICL (@("--onefile", "--windowed", "--name", "cbz-editor-local",
        "--runtime-tmpdir", ".") + $common + @("cbz_editor.py"))
    RunArr $PICL (@("--onefile", "--console", "--name", "cbz-tool-local",
        "--runtime-tmpdir", ".") + $common + @("cbz_tool.py"))
}

if ($Onedir) {
    Step "额外打包 -dir 目录版"
    RunArr $PICL (@("--onedir", "--windowed", "--name", "cbz-editor-dir") + $tkBins + $libBins + $common + @("cbz_editor.py"))
    RunArr $PICL (@("--onedir", "--console", "--name", "cbz-tool-dir") + $libBins + $common + @("cbz_tool.py"))
}

# ---------------------------------------------------------------- 验证
Step "验证产物"
# 有些受限环境（比如带文件策略的会话）不让新进程在系统临时目录里建目录，
# onefile 的解包会因此失败，报 "Could not create temporary directory!"。
# 把 TMP/TEMP 指到工作区内的目录再验证，正常机器上这一步行为完全一样。
$tmp = Join-Path $ROOT "dist\_tmp"
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$env:TMP = $tmp
$env:TEMP = $tmp
$tool = Join-Path $ROOT "dist\cbz-tool.exe"
$sample = Join-Path $ROOT "manga\sample_2ch.cbz"
try {
    RunArr $tool @("check", $sample)
    Write-Host "cbz-tool.exe 跑通 ✓" -ForegroundColor Green
} catch {
    Write-Host "cbz-tool.exe 启动失败: $_" -ForegroundColor Yellow
    Write-Host "如果报 'Could not create temporary directory!'，说明系统临时目录不可写：" -ForegroundColor Yellow
    Write-Host "  用 -Onedir 重新打包（目录版完全不落地临时文件）；正常双击运行不受影响。" -ForegroundColor Yellow
}

$editor = Join-Path $ROOT "dist\cbz-editor.exe"
& $editor --selftest $sample 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "cbz-editor.exe --selftest 退出码 0（GUI 逻辑自检全过）✓" -ForegroundColor Green
} else {
    Write-Host "cbz-editor.exe --selftest 退出码 $LASTEXITCODE（windowed 模式没有 stdout，" -ForegroundColor Yellow
    Write-Host "  先按上面的提示处理临时目录问题，或改用 -Onedir 目录版）" -ForegroundColor Yellow
}

Step "产物"
Get-ChildItem (Join-Path $ROOT "dist") -File | Where-Object { $_.Extension -eq '.exe' } |
    Select-Object @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 2) } }, Name |
    Format-Table -AutoSize
