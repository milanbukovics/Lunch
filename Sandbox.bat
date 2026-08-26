@echo off
title Lunch - SANDBOX (fake data)
cd /d "%~dp0"

rem Try the site out safely. Writes to sandbox.db, never to your real data\ files.
rem Keep this window open while you use it.
python sandbox.py

if errorlevel 1 (
    echo.
    echo Sandbox failed to start. Details above.
    pause
)
