@echo off
REM ============================================================
REM  ENACT launcher
REM  Starts the collector + analyzer engine (main.py) in a
REM  background window, then opens the dashboard. Closing the
REM  dashboard window also asks the engine to shut down.
REM ============================================================

cd /d "%~dp0"
call .venv\Scripts\activate.bat

REM start main.py in a SEPARATE window so it has its own console
REM /b would hide it entirely, but a visible console is better for
REM ENACT specifically: you can see the collectors firing in real
REM time, and Ctrl+C in that window cleanly shuts everything down
start "ENACT Engine" cmd /k "python main.py"

REM give the engine a moment to spin up so the first dashboard
REM frame has data to display. without this, you'll see a brief
REM "no data" flash before collectors populate the database
timeout /t 2 /nobreak >nul

REM the dashboard runs in THIS window, attached to this terminal.
REM closing the dashboard window returns control here, and the
REM batch file exits
python -m src.dashboard.window

REM when the dashboard exits, this batch file ends but the engine
REM keeps running in its own window. that's intentional: you might
REM want to close the dashboard but leave collection going. close
REM the "ENACT Engine" window manually when you're done