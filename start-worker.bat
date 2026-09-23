@echo off
REM Starts the Remote Audit worker.
REM
REM Nothing gets a verdict without this running. Evidence and documents are
REM verified here, in a separate process from the API, because they parse
REM untrusted bytes and that must never happen in the request path.
REM
REM The worker reads .env at startup, so restart it after changing any
REM REMOTE_AUDIT_* setting.
title MTB WORKER
cd /d "%~dp0backend"
echo Starting the Remote Audit worker.
echo Leave this running; evidence and documents stay QUARANTINED without it.
echo.
python remote_audit_worker.py
pause
