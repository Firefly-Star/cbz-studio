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
#   -Local  : 额外产出 *-local.exe（--runtime-tmpdir .，解压到 exe 同级目录，
#             用于 %TEMP% 不可写的受限环境）
#   -Onedir : 额外产出 *-dir\（目录版，启动最快，不落地临时文件）

param(
    [switch]$Local,
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

Step "打包 GUI: cbz-editor.exe"
RunArr $PICL (@("--onefile", "--windowed", "--name", "cbz-editor") + $common + @("cbz_editor.py"))

Step "打包命令行: cbz-tool.exe"
RunArr $PICL (@("--onefile", "--console", "--name", "cbz-tool") + $common + @("cbz_tool.py"))

if ($Local) {
    Step "额外打包 -local 变体（--runtime-tmpdir .）"
    RunArr $PICL (@("--onefile", "--windowed", "--name", "cbz-editor-local",
        "--runtime-tmpdir", ".") + $common + @("cbz_editor.py"))
    RunArr $PICL (@("--onefile", "--console", "--name", "cbz-tool-local",
        "--runtime-tmpdir", ".") + $common + @("cbz_tool.py"))
}

if ($Onedir) {
    Step "额外打包 -dir 目录版"
    RunArr $PICL (@("--onedir", "--windowed", "--name", "cbz-editor-dir") + $common + @("cbz_editor.py"))
    RunArr $PICL (@("--onedir", "--console", "--name", "cbz-tool-dir") + $common + @("cbz_tool.py"))
}

# ---------------------------------------------------------------- 验证
Step "验证产物"
$tool = Join-Path $ROOT "dist\cbz-tool.exe"
$sample = Join-Path $ROOT "manga\sample_2ch.cbz"
try {
    RunArr $tool @("check", $sample)
    Write-Host "cbz-tool.exe 跑通 ✓" -ForegroundColor Green
} catch {
    Write-Host "cbz-tool.exe 启动失败: $_" -ForegroundColor Yellow
    Write-Host "如果报 'Failed to extract ... Permission denied'，说明 %TEMP% 不可写：" -ForegroundColor Yellow
    Write-Host "  用 -Local（--runtime-tmpdir .）或 -Onedir 重新打包；正常双击运行不受影响。" -ForegroundColor Yellow
}

$editor = Join-Path $ROOT "dist\cbz-editor.exe"
& $editor --selftest $sample 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "cbz-editor.exe --selftest 退出码 0（GUI 逻辑自检全过）✓" -ForegroundColor Green
} else {
    Write-Host "cbz-editor.exe --selftest 退出码 $LASTEXITCODE（windowed 模式无 stdout，" -ForegroundColor Yellow
    Write-Host "  可先按上面的提示换 -Local 变体重试）" -ForegroundColor Yellow
}

Step "产物"
Get-ChildItem (Join-Path $ROOT "dist") -File | Where-Object { $_.Extension -eq '.exe' } |
    Select-Object @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 2) } }, Name |
    Format-Table -AutoSize
