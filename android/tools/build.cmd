@echo off
rem 双击即可构建 APK（PowerShell 默认禁止运行脚本，这里显式 Bypass）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 (
  echo.
  echo === 构建失败 ===
  pause
  exit /b 1
)
echo.
echo === 构建完成，APK 在 dist\cbz-studio.apk ===
pause
