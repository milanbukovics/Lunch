@echo off
title Lunch
cd /d "%~dp0"

rem Runs the site locally against the JSON files in data\ (no DATABASE_URL set).
rem Keep this window open while you use it.
python app.py

if errorlevel 1 (
    echo.
    echo Lunch failed to start. Details above.
    pause
)
