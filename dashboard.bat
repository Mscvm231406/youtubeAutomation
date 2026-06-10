@echo off
REM ============================================================
REM  Open the Shorts automation MISSION-CONTROL dashboard.
REM  Double-click this file. It regenerates a fresh snapshot and
REM  serves it locally (http://localhost) in a fullscreen browser
REM  so the #1 short autoplays on loop + telemetry runs live.
REM  This window stays open serving — close it (or Ctrl+C) to stop.
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM Refresh live YouTube stats first (best-effort: needs network + auth).
REM Failure here is non-fatal — the dashboard still renders cached data.
echo Refreshing channel stats (views / subscribers / revenue)...
".venv\Scripts\python.exe" scripts\fetch_stats.py

".venv\Scripts\python.exe" dashboard.py
if errorlevel 1 (
  echo.
  echo Dashboard failed to generate. See the error above.
  pause
)
