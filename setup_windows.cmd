@echo off
setlocal EnableExtensions

set "REPO_DIR=%~dp0"
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"
set "BRANCH=codex/windows-extension-local-bridge"

echo.
echo [1/5] Project folder: "%REPO_DIR%"
cd /d "%REPO_DIR%" || exit /b 1

where git >nul 2>nul
if errorlevel 1 (
  echo [2/5] Git was not found in PATH. Skipping repository update.
) else if exist ".git\" (
  echo [2/5] Updating repository branch: %BRANCH%
  git fetch origin
  git switch %BRANCH%
  if errorlevel 1 git switch --track origin/%BRANCH%
  git pull --ff-only
) else (
  echo [2/5] This folder has no .git directory. Skipping repository update.
)

set "PY_CMD="
py -3.13 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.13"
if not defined PY_CMD (
  py -3.11 --version >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3.11"
)
if not defined PY_CMD (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo Python was not found. Install Python 3.11+ and rerun this file.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [3/5] Creating virtual environment with: %PY_CMD%
  %PY_CMD% -m venv .venv
  if errorlevel 1 exit /b 1
) else (
  echo [3/5] Virtual environment already exists.
)

set "PY=%REPO_DIR%\.venv\Scripts\python.exe"

echo [4/5] Installing Python dependencies.
"%PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [5/5] Creating global launcher: botcv
set "BIN=%USERPROFILE%\bin"
if not exist "%BIN%" mkdir "%BIN%"

set "LAUNCHER=%BIN%\botcv.cmd"
(
  echo @echo off
  echo call "%REPO_DIR%\botcv.cmd" %%*
) > "%LAUNCHER%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = Join-Path $env:USERPROFILE 'bin'; $path = [Environment]::GetEnvironmentVariable('Path', 'User'); if ([string]::IsNullOrWhiteSpace($path)) { [Environment]::SetEnvironmentVariable('Path', $bin, 'User') } elseif (($path -split ';') -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', ($path.TrimEnd(';') + ';' + $bin), 'User') }"
set "PATH=%PATH%;%BIN%"

echo.
echo Done.
echo Start the server from a new terminal with:
echo   botcv
echo.
echo Keep that terminal open while using the Chrome extension.
