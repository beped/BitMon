@echo off
:: BitMon - one-click start. Double-click this file to launch everything.
:: It runs the launcher, which sets up the virtual environment on first run,
:: starts the backend and the persona overlay, then minimizes to the tray.
title BitMon
cd /d "%~dp0"

set "VENV_PYW=%~dp0venv\Scripts\pythonw.exe"
set "VENV_PY=%~dp0venv\Scripts\python.exe"

if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "%~dp0launcher.py"
    goto :eof
)

if exist "%VENV_PY%" (
    start "" "%VENV_PY%" "%~dp0launcher.py"
    goto :eof
)

:: No virtual environment yet - let the launcher create it using system Python.
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.11 or 3.12 from https://python.org and tick "Add Python to PATH".
    pause
    exit /b 1
)

python "%~dp0launcher.py"
