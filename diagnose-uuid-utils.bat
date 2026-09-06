@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

title LuckyD Browser - Diagnose uuid_utils

echo =================================================================
echo  Diagnosing uuid_utils / langchain_core in the GLOBAL Python
echo  that builds LuckyDBrowser.exe (py -3.10)
echo =================================================================
echo.

set "PY=py -3.10"
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo ERROR: "py -3.10" is not available. Aborting.
    pause
    exit /b 1
)

echo [1/3] Testing uuid_utils import (unfrozen, in this Python directly)...
%PY% -c "import uuid_utils; import uuid_utils._uuid_utils; print('  OK  uuid_utils ->', uuid_utils.__file__)"
set "UUID_OK=%errorlevel%"

echo.
echo [2/3] Testing langchain_core.runnables.base import...
%PY% -c "import langchain_core.runnables.base; print('  OK  langchain_core.runnables.base imported fine')"
set "LC_OK=%errorlevel%"

echo.
if "%UUID_OK%"=="0" if "%LC_OK%"=="0" (
    echo =================================================================
    echo  Both imports succeeded in this Python environment directly.
    echo  This means uuid_utils itself is NOT broken globally - the
    echo  problem is specific to the frozen bundle. Re-run
    echo  rebuild-browser.bat is unlikely to help further; this needs a
    echo  closer look at the .spec packaging (paste this output back).
    echo =================================================================
    pause
    exit /b 0
)

echo =================================================================
echo  Found a broken import in the GLOBAL Python. Repairing...
echo =================================================================
echo.

echo [3/3] Force-reinstalling uuid_utils and langchain_core cleanly...
%PY% -m pip uninstall -y uuid_utils uuid-utils >nul 2>&1
%PY% -m pip install --no-cache-dir --force-reinstall uuid_utils
if errorlevel 1 (
    echo ERROR: uuid_utils reinstall failed. See errors above.
    pause
    exit /b 1
)
%PY% -m pip install --no-cache-dir --force-reinstall langchain_core langgraph
if errorlevel 1 (
    echo ERROR: langchain_core/langgraph reinstall failed. See errors above.
    pause
    exit /b 1
)

echo.
echo Re-testing after reinstall...
%PY% -c "import uuid_utils._uuid_utils; import langchain_core.runnables.base; print('  OK  both import cleanly now')"
if errorlevel 1 (
    echo.
    echo =================================================================
    echo  STILL BROKEN after a clean reinstall. This points to something
    echo  deeper (e.g. a missing Visual C++ runtime DLL, or a corrupted
    echo  pip cache/wheel). Paste this full output back for next steps.
    echo =================================================================
    pause
    exit /b 1
)

echo.
echo =================================================================
echo  Fixed! Now rebuilding LuckyDBrowser.exe with the repaired deps...
echo =================================================================
echo.
call rebuild-browser.bat
