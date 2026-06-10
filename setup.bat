@echo off
REM ============================================================
REM  ONE-TIME SETUP for the Reddit -> YouTube Shorts app.
REM  Double-click this file (or run it in a terminal). It:
REM    1. creates the Python virtual environment (.venv)
REM    2. installs all Python packages
REM    3. creates your .env from the template (if missing)
REM    4. runs the setup checker so you can see what's left
REM
REM  Prerequisites you must install yourself first (see SETUP.md):
REM    - Python 3.10+   (python.org, tick "Add to PATH")
REM    - FFmpeg         (winget install Gyan.FFmpeg)
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo ===== Reddit -^> YouTube Shorts : SETUP =====
echo.

REM --- 1. Python present? ---
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python not found on PATH.
    echo     Install Python 3.10+ from https://python.org ^(tick "Add to PATH"^), then re-run.
    pause
    exit /b 1
)

REM --- 2. Create the virtual environment ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    python -m venv .venv
) else (
    echo Virtual environment already exists - reusing it.
)

REM --- 3. Install dependencies ---
echo.
echo Installing Python packages ^(this can take a few minutes^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

REM --- 4. Create .env from the template if it doesn't exist ---
echo.
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from template. Open it and paste in your keys.
) else (
    echo .env already exists - leaving it untouched.
)

REM --- 5. Run the checker ---
echo.
echo ===== Setup check =====
".venv\Scripts\python.exe" scripts\check_setup.py

echo.
echo ===== Setup finished =====
echo Next: open .env and add your keys, then see SETUP.md to authorize YouTube.
echo This window will stay open. Press any key to close it.
pause >nul
