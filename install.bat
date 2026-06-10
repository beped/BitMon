@echo off
:: ============================================================
::  BitMon - first-time installer
::  Double-click this file once to set everything up.
::  After that, open BitMon from the "BitMon Launcher" shortcut
::  it creates, or by double-clicking start.bat.
:: ============================================================
setlocal
title BitMon installer
cd /d "%~dp0"

set "ROOT=%~dp0"
set "ROOTNB=%ROOT:~0,-1%"
set "VENV=%ROOT%venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_PYW=%VENV%\Scripts\pythonw.exe"

echo.
echo ============================================
echo   BitMon - first-time setup
echo ============================================
echo.

:: 1) Make sure Python is available -------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo.
    echo Install Python 3.11 or 3.12 from https://python.org
    echo and tick "Add Python to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)

:: 2) Ask CPU vs NVIDIA GPU ----------------------------------------------------
echo Speech recognition can run on the CPU (works everywhere) or use an
echo NVIDIA GPU (much faster). Which do you want?
echo.
echo    [1] CPU only          (simplest - choose this if unsure)
echo    [2] NVIDIA GPU (CUDA)  (faster, needs an NVIDIA card)
echo.
set "GPU=1"
set /p GPU=Type 1 or 2 and press Enter [default 1]:
echo.

:: 3) Create the virtual environment ------------------------------------------
if exist "%VENV_PY%" (
    echo Virtual environment already exists - reusing venv.
) else (
    echo Creating virtual environment in venv ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
echo.

:: 4) Upgrade pip --------------------------------------------------------------
echo Upgrading pip ...
"%VENV_PY%" -m pip install --upgrade pip
echo.

:: 5) Install dependencies -----------------------------------------------------
if "%GPU%"=="2" (
    echo Installing CUDA PyTorch wheels first ...
    "%VENV_PY%" -m pip install -r "%ROOT%requirements-gpu.txt"
    if errorlevel 1 goto :pipfail
    echo.
)

echo Installing BitMon dependencies - this can take a few minutes ...
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto :pipfail

:: Record that dependencies are installed so the first launch skips re-installing.
"%VENV_PY%" -c "import hashlib,time;from pathlib import Path;bk=Path(r'%ROOTNB%');d=hashlib.sha256();[(d.update(fn.encode()),d.update(b'\n'),d.update((bk/fn).read_bytes()),d.update(b'\n')) for fn in ('requirements.txt','requirements-core.txt') if (bk/fn).exists()];v=bk/'venv';(v/'.bitmon_requirements_installed').write_text(str(time.time()));(v/'.bitmon_requirements_hash').write_text(d.hexdigest())" 2>nul

:: 6) Create a nice "BitMon Launcher" shortcut (Desktop + this folder) ---------
echo Creating the BitMon Launcher shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $targets=@([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'BitMon Launcher.lnk'), (Join-Path '%ROOT%' 'BitMon Launcher.lnk')); foreach($t in $targets){ $s=$ws.CreateShortcut($t); $s.TargetPath='%VENV_PYW%'; $s.Arguments='launcher.py'; $s.WorkingDirectory='%ROOT%'; $s.IconLocation='%ROOT%web\app-icon.ico'; $s.Description='BitMon Launcher'; $s.Save() }" 2>nul

echo.
echo ============================================
echo   Done! BitMon is installed.
echo   Open it from the "BitMon Launcher" shortcut
echo   on your Desktop, or run start.bat.
echo ============================================
echo.

choice /c YN /m "Start BitMon now"
if errorlevel 2 goto :eof
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "%ROOT%launcher.py"
) else (
    start "" "%VENV_PY%" "%ROOT%launcher.py"
)
goto :eof

:pipfail
echo.
echo [ERROR] Dependency installation failed. Scroll up to see what went wrong.
echo If it was a network hiccup, just run install.bat again.
pause
exit /b 1
