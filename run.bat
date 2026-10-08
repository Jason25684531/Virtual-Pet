@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo Python virtual environment not found: venv\Scripts\python.exe
  exit /b 1
)

"venv\Scripts\python.exe" --version >nul 2>&1
if errorlevel 1 (
  echo Python virtual environment is invalid. Recreate venv with Python 3.11, then reinstall requirements.
  exit /b 1
)

"venv\Scripts\python.exe" main.py
exit /b %errorlevel%
