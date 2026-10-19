@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

title LuckyD Browser - Rebuild

echo =================================================================
echo  LuckyD Browser Rebuild
echo  Fixes: "No module named 'uuid_utils._uuid_utils'" error
echo  by rebuilding the frozen .exe your Desktop shortcut actually launches
echo  ( coding-agent\browser\dist\LuckyDBrowser\LuckyDBrowser.exe )
echo  NOTE: the Desktop "LuckyD Browser.lnk" points at browser\dist, NOT
echo  the top-level dist\ folder, so this script builds browser\dist.
echo =================================================================
echo.

REM -- Gentle pre-build: ask a running browser to exit cleanly, never force-kill.
REM PyInstaller fails on locked files if the old exe is still running. We send
REM WM_CLOSE via PowerShell (lets Qt save session + release handles), wait up
REM to 60s, and only then build. If it is still running we SKIP -- never kill.
powershell -NoProfile -ExecutionPolicy RemoteSigned -Command "$p = Get-Process -Name LuckyDBrowser -ErrorAction SilentlyContinue; if ($p) { $p.CloseMainWindow() | Out-Null }" >nul 2>&1
set /a WAITCOUNT=0
:waitloop
tasklist /FI "IMAGENAME eq LuckyDBrowser.exe" 2>nul | find /I "LuckyDBrowser.exe" >nul
if errorlevel 1 goto notrunning
timeout /t 5 /nobreak >nul
set /a WAITCOUNT+=1
if %WAITCOUNT% GEQ 12 goto stillrunning
goto waitloop
:stillrunning
echo.
echo NOTE: LuckyD Browser is still running. Close it once (Alt+F4 is fine),
echo then re-run this script -- the build will continue cleanly.
echo Refusing to force-kill a running browser (crash-safe mode).
pause
exit /b 2
:notrunning
echo       Browser is closed - safe to build.
echo.

REM -- Step 2: Find Python (prefer project venv, then Python 3.10-3.12) --
echo [2/4] Locating Python...
set "PYTHON="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
) else (
    py -3.10 -c "import sys" >nul 2>&1
    if !errorlevel! == 0 (
        set "PYTHON=py -3.10"
    ) else (
        where python >nul 2>&1
        if !errorlevel! == 0 (
            set "PYTHON=python"
        )
    )
)

if not defined PYTHON (
    echo.
    echo ERROR: Python not found on PATH.
    echo Install Python 3.10-3.12 from https://www.python.org/downloads/
    echo and make sure "Add Python to PATH" is checked during install.
    echo.
    pause
    exit /b 1
)
echo       Using: !PYTHON!
echo.

REM -- Step 3: Make sure PyInstaller is available --
echo [3/4] Checking PyInstaller is installed...
%PYTHON% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       Installing PyInstaller...
    %PYTHON% -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install PyInstaller. See errors above.
        pause
        exit /b 1
    )
)
echo       OK.
echo.

REM -- Step 4: Rebuild into browser\dist (the folder the Desktop shortcut uses) --
REM Do NOT pass --distpath ..\dist: PyInstaller's default distpath is
REM browser\dist, which is exactly where "LuckyD Browser.lnk" points.
REM (A previous version built into top-level dist\, leaving the launched
REM 4:22 AM exe stale and still crashing on uuid_utils.)
echo [4/4] Rebuilding LuckyDBrowser.exe...
echo       This can take a few minutes - please wait.
echo.
cd browser
%PYTHON% -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
set "BUILD_RESULT=%errorlevel%"
cd ..

echo.
if not "%BUILD_RESULT%"=="0" (
    echo =================================================================
    echo  BUILD FAILED (exit code %BUILD_RESULT%^). See the errors above.
    echo =================================================================
    pause
    exit /b %BUILD_RESULT%
)

echo =================================================================
echo  Build complete!
echo  Rebuilt: coding-agent\browser\dist\LuckyDBrowser\LuckyDBrowser.exe
echo  (this is the exact file your Desktop shortcut points to)
echo.
echo  Launch it from your pinned taskbar icon and try a research query.
echo  To confirm the fix worked, check for a NEW folder appearing under:
echo    coding-agent\data\deep_research\runs\
echo  A "uuid_utils" WARN line in events.log is fine (graceful fallback).
echo  A hard "Error: ..." line means something is still wrong.
echo =================================================================
echo.
pause
