@echo off
REM ============================================================
REM  Parallel Shorts Factory: build N videos at once, then
REM  upload each to ALL channels in parallel, with a live
REM  terminal dashboard (progress bars) you can watch.
REM  Double-click this file, or run it from a terminal.
REM  Optional args pass straight through, e.g.:
REM      run_parallel.bat --limit 3
REM      run_parallel.bat --limit 1 --no-upload
REM      run_parallel.bat --upload-workers 6
REM  With no args it builds run.daily_quota Shorts -> all channels.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo ===== PARALLEL Reddit -^> YouTube Shorts : BUILD + UPLOAD (ALL CHANNELS) =====
echo Working dir: %cd%
echo Command: run_parallel.py %*
echo.

".venv\Scripts\python.exe" run_parallel.py %*
set "RUN_EXIT=%errorlevel%"

REM --- Clean up generated video files in output\ to save storage space ---
echo.
echo Cleaning up video files in output\ ...
del /q "output\*.mp4" "output\*.mov" "output\*.mkv" "output\*.avi" "output\*.webm" 2>nul

echo.
echo ===== Finished (exit code %RUN_EXIT%) =====
echo This window will stay open. Press any key to close it.
pause >nul
