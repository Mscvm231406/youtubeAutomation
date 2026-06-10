@echo off
REM ============================================================
REM  Upload videos ALREADY rendered in output\ to YouTube.
REM  (Does NOT build anything. Skips files already uploaded.)
REM  Double-click this file, or run it from a terminal.
REM  Optional args pass straight through, e.g.:
REM      run_upload.bat --privacy unlisted
REM      run_upload.bat output\AskReddit_iwedc5.mp4
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo ===== Reddit -^> YouTube Shorts : UPLOAD EXISTING =====
echo Working dir: %cd%
echo Command: scripts\upload_existing.py %*
echo.

".venv\Scripts\python.exe" scripts\upload_existing.py %*

echo.
echo ===== Finished (exit code %errorlevel%) =====
echo This window will stay open. Press any key to close it.
pause >nul
