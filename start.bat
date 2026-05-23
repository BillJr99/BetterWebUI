@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set CLK_STARTED=0
set AUTOGUI_STARTED=0
set OSSO_STARTED=0

echo =================================
echo   BetterWebUI -- Windows launcher
echo =================================
echo.

REM ── Check Python ──────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not found.
    echo.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo or run:  winget install Python.Python.3.12
    echo.
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 goto :py_too_old
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 goto :py_too_old
goto :py_ok

:py_too_old
echo Python %PY_VER% found, but 3.10+ is required.
echo Install from https://www.python.org/downloads/ or run: winget install Python.Python.3.12
pause
exit /b 1

:py_ok

REM ── Check Git ─────────────────────────────────────────────────────────────────
git --version >nul 2>&1
if errorlevel 1 (
    echo git is not found.
    echo.
    echo Install Git from https://git-scm.com/downloads
    echo or run:  winget install Git.Git
    echo.
    pause
    exit /b 1
)

REM ── Pull submodules ───────────────────────────────────────────────────────────
echo Updating git submodules...
git submodule update --init --recursive
if errorlevel 1 (
    echo ERROR: Could not update git submodules.
    pause
    exit /b 1
)

REM ── BetterWebUI virtualenv ────────────────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: installing BetterWebUI Python packages...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Could not create Python environment.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM ── Interactive setup wizard ───────────────────────────────────────────────────
REM Validates deploy\.env, prompts for anything missing or broken, then saves.
python scripts\setup_wizard.py
if errorlevel 1 (
    echo.
    echo Setup wizard cancelled. Re-run start.bat to try again.
    pause
    exit /b 1
)

REM ── Load deploy\.env (written/updated by wizard) ───────────────────────────────
if exist "deploy\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("deploy\.env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

REM ── Apply port defaults ────────────────────────────────────────────────────────
if "%CLK_PORT%"==""    set CLK_PORT=8001
if "%AUTOGUI_PORT%"="" set AUTOGUI_PORT=8002
if "%OSSO_PORT%"==""   set OSSO_PORT=5001
if "%PORT%"==""        set PORT=8765

REM ── Derive service base-URLs for BetterWebUI ──────────────────────────────────
if "%CLK_BASE_URL%"==""    set CLK_BASE_URL=http://localhost:%CLK_PORT%
if "%AUTOGUI_BASE_URL%"="" set AUTOGUI_BASE_URL=http://localhost:%AUTOGUI_PORT%
if "%OSSO_BASE_URL%"==""   set OSSO_BASE_URL=http://localhost:%OSSO_PORT%

REM ── Convenience aliases for service-launch blocks ─────────────────────────────
set OW_URL=%OPENWEBUI_BASE_URL%
set OW_KEY=%OPENWEBUI_API_KEY%
set OW_MODEL=%OPENWEBUI_MODEL%
if "%LLM_PROVIDER%"=="" set LLM_PROVIDER=openwebui
set OW_PROVIDER=%LLM_PROVIDER%

REM ── CognitiveLoopKernel ───────────────────────────────────────────────────────
call :is_up http://localhost:%CLK_PORT%/api/healthz
if %ERRORLEVEL%==0 (
    echo CognitiveLoopKernel already running on port %CLK_PORT% -- skipping.
) else (
    echo Starting CognitiveLoopKernel...
    call :setup_venv "CognitiveLoopKernel"
    START "BetterWebUI-CLK" /MIN cmd /c "cd /d "%~dp0CognitiveLoopKernel" && set CLK_API_PORT=%CLK_PORT% && set CLK_WORKSPACES_DIR=%CLK_WORKSPACES_DIR% && set CLK_PROVIDER=%OW_PROVIDER% && set CLK_OPENWEBUI_ENDPOINT=%OW_URL% && set CLK_OPENWEBUI_API_KEY=%OW_KEY% && set CLK_OPENWEBUI_MODEL=%OW_MODEL% && .venv\Scripts\python.exe -m clk_harness.api"
    set CLK_STARTED=1
)

REM ── AutoGUI ───────────────────────────────────────────────────────────────────
call :is_up http://localhost:%AUTOGUI_PORT%/api/healthz
if %ERRORLEVEL%==0 (
    echo AutoGUI already running on port %AUTOGUI_PORT% -- skipping.
) else (
    echo Starting AutoGUI...
    call :setup_venv "AutoGUI"
    START "BetterWebUI-AutoGUI" /MIN cmd /c "cd /d "%~dp0AutoGUI" && set AUTOGUI_API_PORT=%AUTOGUI_PORT% && set OPENWEBUI_BASE_URL=%OW_URL% && set OPENWEBUI_API_KEY=%OW_KEY% && set OPENWEBUI_MODEL=%OW_MODEL% && .venv\Scripts\python.exe api.py"
    set AUTOGUI_STARTED=1
)

REM ── OSScreenObserver ──────────────────────────────────────────────────────────
call :is_up http://localhost:%OSSO_PORT%/api/healthz
if %ERRORLEVEL%==0 (
    echo OSScreenObserver already running on port %OSSO_PORT% -- skipping.
) else (
    echo Starting OSScreenObserver...
    call :setup_venv "OSScreenObserver"
    START "BetterWebUI-OSSO" /MIN cmd /c "cd /d "%~dp0OSScreenObserver" && set CLK_PROVIDER=%OW_PROVIDER% && set CLK_OPENWEBUI_ENDPOINT=%OW_URL% && set CLK_OPENWEBUI_API_KEY=%OW_KEY% && set CLK_OPENWEBUI_MODEL=%OW_MODEL% && .venv\Scripts\python.exe main.py"
    set OSSO_STARTED=1
)

echo.
echo BetterWebUI is starting on http://127.0.0.1:%PORT%
echo Open that link in your browser. Close this window to stop.
echo.

".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port %PORT%

REM ── Cleanup (runs after uvicorn exits) ────────────────────────────────────────
if %CLK_STARTED%==1     TASKKILL /FI "WINDOWTITLE eq BetterWebUI-CLK"     /T /F >nul 2>&1
if %AUTOGUI_STARTED%==1 TASKKILL /FI "WINDOWTITLE eq BetterWebUI-AutoGUI" /T /F >nul 2>&1
if %OSSO_STARTED%==1    TASKKILL /FI "WINDOWTITLE eq BetterWebUI-OSSO"    /T /F >nul 2>&1

goto :eof

REM ── Subroutines ───────────────────────────────────────────────────────────────

:is_up
REM Uses PowerShell to probe a URL. Sets ERRORLEVEL 0 if reachable, 1 if not.
powershell -NoProfile -Command ^
  "try { $null = Invoke-WebRequest '%~1' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; exit 0 } catch { exit 1 }" >nul 2>&1
goto :eof

:setup_venv
REM %~1 is the submodule directory name (relative to repo root)
if not exist "%~1\.venv\Scripts\python.exe" (
    python -m venv "%~1\.venv"
)
if exist "%~1\requirements.txt" (
    "%~1\.venv\Scripts\pip.exe" install -q --upgrade pip >nul
    "%~1\.venv\Scripts\pip.exe" install -q -r "%~1\requirements.txt"
) else if exist "%~1\pyproject.toml" (
    "%~1\.venv\Scripts\pip.exe" install -q --upgrade pip >nul
    "%~1\.venv\Scripts\pip.exe" install -q -e "%~1"
)
goto :eof
