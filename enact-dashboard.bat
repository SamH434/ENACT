@echo off
REM ============================================================
REM  ENACT launcher
REM  Starts the engine in a background window, runs the dashboard,
REM  and shuts the engine down when the dashboard closes.
REM ============================================================

cd /d "%~dp0"
call .venv\Scripts\activate.bat

start "ENACT Engine" cmd /k "python main.py"
timeout /t 2 /nobreak >nul

python -m src.dashboard.window

REM dashboard closed, clean up the engine window
taskkill /FI "WINDOWTITLE eq ENACT Engine" /T >nul 2>&1