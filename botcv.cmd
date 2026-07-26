@echo off
setlocal EnableExtensions

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"

cd /d "%REPO_DIR%" || exit /b 1

set "PY=%REPO_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Virtual environment not found: "%PY%"
  echo Run setup_windows.cmd first.
  exit /b 1
)

"%PY%" -m src.antibot_cv.automation.controller control-server --config config\automation.local.json --live %*
