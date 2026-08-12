@echo off
REM Start-LuckyD-Cline.bat
REM One-click launcher: start the ClinePass bridge, then open LuckyD Code.
REM The bridge lets the host app use your free Cline CLI login (no paid key).

setlocal
set "AGENT_DIR=C:\Users\dylan\OneDrive\Desktop\coding-agent"
set "BRIDGE_PORT=8317"
set "APP_EXE=C:\Users\dylan\AppData\Local\Programs\LuckyDBrowser\LuckyDBrowser.exe"

REM --- Is the bridge already responding? ---
powershell -NoProfile -Command "try{(Invoke-RestMethod -Uri 'http://127.0.0.1:%BRIDGE_PORT%/v1/health' -TimeoutSec 2).ok}catch{'false'}" | findstr /i "True" >nul
if %errorlevel%==0 goto :bridge_up

echo Starting ClinePass bridge on port %BRIDGE_PORT% ...
start "ClineBridge" /min cmd /c "cd /d "%AGENT_DIR%" && python cline_bridge.py"

REM --- Wait for the bridge to become healthy (up to ~20s) ---
set /a tries=0
:wait_loop
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(Invoke-RestMethod -Uri 'http://127.0.0.1:%BRIDGE_PORT%/v1/health' -TimeoutSec 2).ok}catch{'false'}" | findstr /i "True" >nul
if %errorlevel%==0 goto :bridge_up
set /a tries+=1
if %tries% lss 20 goto :wait_loop
echo WARNING: bridge did not report healthy in time. Launching app anyway.

:bridge_up
echo Bridge is up. Launching LuckyD Code ...
start "" "%APP_EXE%"
endlocal
