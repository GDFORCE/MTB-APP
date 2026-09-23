@echo off
REM Starts the MTB API on all interfaces so a phone on the same Wi-Fi can
REM reach it. --host 0.0.0.0 is the part that matters: the default (127.0.0.1)
REM is reachable only from this PC, and Expo Go would time out silently.
REM
REM Keep this window open during a demo; request logs appear here.
title MTB API
cd /d "%~dp0backend"
echo Starting MTB API on http://0.0.0.0:8000
echo Your phone should use the LAN address, not localhost.
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
