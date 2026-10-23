@echo off
title Switch Cline Account
echo ============================================
echo    Cline Account Switcher
echo ============================================
echo.
echo Current account: dylanchess03@gmail.com
echo Target account:  dylanchess02@gmail.com
echo.
echo This will:
echo  1. Backup current secrets.json
echo  2. Remove the stored auth token
echo  3. Open Cline auth for you to log in with the new account
echo.
set /p confirm="Proceed? (y/n): "
if /i "%confirm%" neq "y" exit /b

echo.
echo [1/3] Backing up secrets.json...
copy "%USERPROFILE%\.cline\data\secrets.json" "%USERPROFILE%\.cline\data\secrets.json.backup" >nul
if %ERRORLEVEL% equ 0 ( echo    Backup created: secrets.json.backup ) else ( echo    FAILED && pause && exit /b )

echo [2/3] Clearing auth token...
echo {} > "%USERPROFILE%\.cline\data\secrets.json"
if %ERRORLEVEL% equ 0 ( echo    Token cleared successfully ) else ( echo    FAILED && pause && exit /b )

echo [3/3] Launching Cline auth...
echo.
echo A browser window will open. Log in with: dylanchess02@gmail.com
echo.
start "" "C:\Users\dylan\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe" auth cline
echo.
echo Done! Follow the browser prompts to log in.
echo If you need to restore the old token, run:
echo   copy "%USERPROFILE%\.cline\data\secrets.json.backup" "%USERPROFILE%\.cline\data\secrets.json"
echo.
pause
