@echo off
title LuckyD Browser — Install
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ======================================================
echo   LuckyD Browser — Install as Application
echo ======================================================
echo.

:: Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install Python from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Install/upgrade browser dependencies
echo [1/4] Installing/upgrading dependencies...
python -m pip install -r "%~dp0requirements.txt" --quiet
if %errorlevel% neq 0 (
    echo WARNING: pip install had issues. Continuing anyway...
)

:: Create Start Menu shortcut (using PowerShell)
echo [2/4] Creating Start Menu shortcut...
set "SHORTCUT_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\LuckyD Browser"
if not exist "%SHORTCUT_DIR%" mkdir "%SHORTCUT_DIR%"

set "PS_CMD=^
    $ws = New-Object -ComObject WScript.Shell; ^
    $sc = $ws.CreateShortcut('%SHORTCUT_DIR%\LuckyD Browser.lnk'); ^
    $sc.TargetPath = '%~dp0run_browser.bat'; ^
    $sc.WorkingDirectory = '%~dp0'; ^
    $sc.Description = 'LuckyD Browser - Chromium-based browser with AI assistant'; ^
    $sc.IconLocation = '%~dp0assets\icon.ico,0' -replace '\\','/'; ^
    if (-not (Test-Path ($sc.IconLocation -replace ',0$'))) { $sc.IconLocation = '%%SystemRoot%%\system32\imageres.dll,14'; }; ^
    $sc.Save(); ^
    if (Test-Path '%SHORTCUT_DIR%\LuckyD Browser.lnk') { Write-Output 'OK' } else { Write-Output 'FAIL' }"

for /f %%R in ('powershell -NoProfile -Command "!PS_CMD!"') do set "RESULT=%%R"
if "!RESULT!"=="OK" (
    echo   + Start Menu shortcut created
) else (
    echo   ~ Could not create Start Menu shortcut (run as admin if needed)
)

:: Add uninstall registry entry
echo [3/4] Registering uninstaller...
set "UNINSTALL_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LuckyDBrowser"
reg add "%UNINSTALL_KEY%" /f /v "DisplayName" /t REG_SZ /d "LuckyD Browser" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "UninstallString" /t REG_SZ /d "\"%~dp0uninstall_browser.bat\"" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "InstallLocation" /t REG_SZ /d "%~dp0" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "DisplayIcon" /t REG_SZ /d "%~dp0assets\icon.ico" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "Publisher" /t REG_SZ /d "LuckyD" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "DisplayVersion" /t REG_SZ /d "1.0.0" >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "NoModify" /t REG_DWORD /d 1 >nul 2>&1
reg add "%UNINSTALL_KEY%" /f /v "NoRepair" /t REG_DWORD /d 1 >nul 2>&1
echo   + Uninstaller registered

:: Optional: Pin to taskbar (user choice)
echo [4/4] Optional: Pin to taskbar / desktop?
echo   You can now pin LuckyD Browser to your taskbar:
echo     Right-click Start Menu ^> "LuckyD Browser" ^> Pin to taskbar
echo.

echo ======================================================
echo   Installation complete!
echo.
echo   Launch from Start Menu: LuckyD Browser
echo   Or run: run_browser.bat
echo.
echo   To uninstall, run: uninstall_browser.bat
echo ======================================================
echo.
pause