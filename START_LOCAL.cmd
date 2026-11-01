@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto run
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv .venv
) else (
  if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
  ) else (
    echo Python 3.12 is required. Install it from https://www.python.org/downloads/
    exit /b 1
  )
)
if errorlevel 1 exit /b 1
:run
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m scripts.run_local %*
