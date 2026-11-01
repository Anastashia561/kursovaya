@echo off
setlocal
cd /d "%~dp0"
if /i "%~1"=="--smoke" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1" -Smoke
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1"
)
exit /b %errorlevel%
