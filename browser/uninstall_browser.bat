@echo off
title LuckyD Browser — Uninstall
cd /d "%~dp0"

echo ======================================================
echo   LuckyD Browser — Uninstall
echo ======================================================
echo.

:: Confirm
echo Are you sure you want to uninstall LuckyD Browser?
set /p CONFIRM="Type YES to confirm: "
if /i not "%CONFIRM%"=="YES" (
    echo Uninstall cancelled.
    pause
    exit /b 0
)

:: Remove Start Menu shortcut
echo [1/3] Removing Start Menu shortcut...
set "SHORTCUT_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\LuckyD Browser"
if exist "%SHORTCUT_DIR%\LuckyD Browser.lnk" (
    del "%SHORTCUT_DIR%\LuckyD Browser.lnk" /f /q
    echo   + Shortcut removed
)
if exist "%SHORTCUT_DIR%" (
    rmdir "%SHORTCUT_DIR%" 2>nul
)

:: Remove uninstall registry entry
echo [2/3] Removing uninstall registry entry...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LuckyDBrowser" /f >nul 2>&1
echo   + Registry entry removed

:: Optionally clear app data
echo [3/3] Clear app data?
set /p CLEAR="Delete saved settings and browsing data? (y/N): "
if /i "%CLEAR%"=="y" (
    if exist "%LOCALAPPDATA%\luckyd-browser" (
        rmdir /s /q "%LOCALAPPDATA%\luckyd-browser"
        echo   + App data removed
    )
    if exist "%USERPROFILE%\.luckyd-browser" (
        rmdir /s /q "%USERPROFILE%\.luckyd-browser"
        echo   + App data removed
    )
    echo   + To remove FMHY cache: delete data\fmhy.json manually
)

echo.
echo ======================================================
echo   Uninstall complete.
echo   The browser folder itself was NOT deleted.
echo   Delete it manually if desired: %~dp0
echo ======================================================
echo.
pause