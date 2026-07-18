@echo off
REM ============================================================
REM  ENACT silent launcher (invoked by enact.vbs)
REM  Runs the engine and dashboard without visible terminals.
REM  All output goes to logs/ instead of stdout.
REM ============================================================

cd /d "%~dp0"

REM activate venv silently (call so we return here after)
call .venv\Scripts\activate.bat

REM start the engine, redirecting all output to a session log file.
REM the start /b flag runs it in this process's console (already hidden by vbs).
REM 2>&1 merges stderr into stdout so any crash trace still gets captured
start /b "" cmd /c "python main.py > logs\engine-session.log 2>&1"

REM small delay so the engine has time to spin up and populate the db before
REM the dashboard opens and starts polling
timeout /t 3 /nobreak > nul

REM start the dashboard, also with output captured to a log
start /b "" cmd /c "python -m src.dashboard.window > logs\dashboard-session.log 2>&1"

REM this batch file exits immediately, but both child processes continue.
REM they'll close on their own when the dashboard window is closed
exit