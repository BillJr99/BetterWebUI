@echo off
REM BetterWebUI launcher for Windows.
REM First run installs Python deps in a local virtualenv. After that it just starts.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating a Python environment and installing packages...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo Could not create the Python environment. Make sure Python 3.10+ is installed
    echo from python.org and that "python" works in this terminal.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if "%PORT%"=="" set PORT=8765

echo.
echo BetterWebUI is starting on http://127.0.0.1:%PORT%
echo Open that link in your browser. Close this window to stop.
echo.

".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port %PORT%
