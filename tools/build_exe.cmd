@echo off
rem 双击即可打包 Windows exe（PowerShell 默认禁止运行脚本，这里显式 Bypass）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1" %*
if errorlevel 1 (
  echo.
  echo === 打包失败 ===
  pause
  exit /b 1
)
echo.
echo === 打包完成，exe 在 dist\ ===
pause
