@echo off
echo ===== Checking Cline Auth Status =====
echo.
"C:\Users\dylan\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe" auth whoami
echo.
echo ===== Available Auth Commands =====
"C:\Users\dylan\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe" auth --help
echo.
echo ===== Checking config locations =====
dir "%APPDATA%\cline" 2>nul
echo.
dir "%APPDATA%\Code\User\globalStorage" 2>nul | findstr cline
echo.
echo ===== Checking npm global =====
where cline 2>nul
echo.
pause
