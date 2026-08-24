@echo off
setlocal
title LuckyD Browser - Apply Updates
cd /d "%~dp0"

echo =================================================================
echo  LuckyD Browser - applying icon + build updates
echo  Running from: %cd%
echo =================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python is not in your PATH.
    echo Install Python from https://www.python.org/downloads/
    echo and check "Add Python to PATH" during install, then re-run this script.
    echo.
    pause
    exit /b 1
)

echo [1/4] Regenerating the browser icon...
python make_icon.py
if %errorlevel% neq 0 (
    echo   FAILED - see error above. Common cause: Pillow not installed.
    echo   Try:  pip install pillow
    pause
    exit /b 1
)
echo   OK - browser\assets\icon.png and icon.ico updated.
echo.

echo [2/4] Installing/updating Python dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo   WARNING: dependency install had issues - continuing anyway.
)
echo.

echo [3/4] Rebuilding the app bundle with PyInstaller (this can take a few minutes)...
python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
if %errorlevel% neq 0 (
    echo   FAILED - see error above. Common cause: PyInstaller not installed.
    echo   Try:  pip install pyinstaller
    pause
    exit /b 1
)
echo   OK - rebuilt app is in browser\dist\LuckyDBrowser
echo.

echo [4/4] Building the installer (requires Inno Setup 6)...
if exist "installer\build_installer.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "installer\build_installer.ps1"
    if %errorlevel% neq 0 (
        echo   WARNING: installer build failed - likely Inno Setup 6 isn't installed.
        echo   Get it from: https://jrsoftware.org/isinfo.php
        echo   The rebuilt app in browser\dist\LuckyDBrowser still works fine without this step.
    ) else (
        echo   OK - installer written to browser\installer\output\
    )
) else (
    echo   Skipped - installer\build_installer.ps1 not found.
)

echo.
echo =================================================================
echo  Done. Next: open a terminal and run "python main.py", then type
echo  /model to confirm you're on the OpenRouter free model.
echo =================================================================
echo.
pause
