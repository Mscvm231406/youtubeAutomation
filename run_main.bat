@echo off
REM ============================================================
REM  Build a new Reddit Short AND upload it to YouTube.
REM  Double-click this file, or run it from a terminal.
REM  Optional args pass straight through, e.g.:
REM      run_main.bat --limit 1
REM      run_main.bat --subreddit tifu --limit 2
REM  With no args it uses run.daily_quota from config.yaml.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo ===== Reddit -^> YouTube Shorts : BUILD + UPLOAD (ALL CHANNELS) =====
echo Working dir: %cd%
echo Command: run_main.py %*
echo.

".venv\Scripts\python.exe" run_main.py %*
set "RUN_EXIT=%errorlevel%"

REM --- Clean up generated video files in output\ to save storage space ---
echo.
echo Cleaning up video files in output\ ...
del /q "output\*.mp4" "output\*.mov" "output\*.mkv" "output\*.avi" "output\*.webm" 2>nul

echo.
echo ===== Finished (exit code %RUN_EXIT%) =====
echo This window will stay open. Press any key to close it.
pause >nul
