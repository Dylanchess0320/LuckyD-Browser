@echo off
title LuckyD Browser
cd /d "%~dp0"

:: Try pythonw first (runs without a console window)
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" /b pythonw main.py %*
    exit
)

:: Fallback to python (shows a console window)
where python >nul 2>nul
if %errorlevel% equ 0 (
    echo [LuckyD] pythonw not found, using python (console visible)...
    start "" /b python main.py %*
    exit
)

:: Neither found — show helpful error
echo.
echo =================================================================
echo  ERROR: Python is not in your PATH.
echo.
echo  Install Python from https://www.python.org/downloads/
echo  Make sure to check "Add Python to PATH" during installation.
echo =================================================================
echo.
pause
