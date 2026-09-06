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

REM -- Step 1: Close any running LuckyD Browser so its files aren't locked --
echo [1/4] Closing LuckyD Browser if it's running...
taskkill /IM LuckyDBrowser.exe /F >nul 2>&1
if !errorlevel! == 0 (
    echo       Closed a running instance.
) else (
    echo       Not currently running - nothing to close.
)
REM Give Windows a moment to release file handles.
timeout /t 2 /nobreak >nul
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
