@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title LuckyD Code - Health Check

REM -- Find Python (prefer project venv, then Python 3.10-3.12) --
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
        python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,13) else 1)" >nul 2>&1
        if !errorlevel! == 0 (
            set "PYTHON=python"
        ) else (
            where python >nul 2>&1
            if !errorlevel! == 0 (
                set "PYTHON=python"
            )
        )
    )
)
if not defined PYTHON (
    echo Error: Python not found.
    pause
    exit /b 1
)

REM -- Python version check --
for /f "tokens=2" %%v in ('%PYTHON% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%v"
echo Using Python !PYVER! (%PYTHON%)
echo.
%PYTHON% -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,13) else 1)"
if errorlevel 1 (
    echo [WARN] pyproject.toml requires Python 3.10-3.12; this interpreter is !PYVER!.
    echo        If installs below fail with odd build/wheel errors, install
    echo        Python 3.12 from python.org and re-run this script with:
    echo          py -3.12 verify.bat   (or create a venv with py -3.12 -m venv .venv)
    echo.
)

REM -- Ensure dev + browser deps are installed --
echo ========================================
echo  Checking dependencies
echo ========================================
echo Installing/verifying dev dependencies from requirements-dev.txt ...
%PYTHON% -m pip install -q -r requirements-dev.txt
echo Installing/verifying browser dependencies from browser\requirements.txt ...
%PYTHON% -m pip install -q -r browser\requirements.txt
echo.

REM -- Run each check as its own step --
set "MARK_DIR=%TEMP%\luckyd-verify-%RANDOM%"
mkdir "%MARK_DIR%" >nul 2>&1

echo ========================================
echo  1/3  pytest
echo ========================================
%PYTHON% -m pytest -q
call :record pytest %errorlevel%

echo.
echo ========================================
echo  2/3  mkdocs build
echo ========================================
%PYTHON% -m mkdocs build --strict
call :record mkdocs %errorlevel%

echo.
echo ========================================
echo  3/3  browser selftest (111 checks)
echo ========================================
if exist "browser\selftest.py" (
    %PYTHON% browser\selftest.py
    call :record selftest %errorlevel%
) else (
    echo [SKIP] browser\selftest.py not found
)

echo.
echo ========================================
set "ANY_FAILED=0"
for %%f in (pytest mkdocs selftest) do (
    if exist "%MARK_DIR%\%%f.fail" set "ANY_FAILED=1"
)
if "%ANY_FAILED%"=="0" (
    echo   ALL CHECKS PASSED
) else (
    echo   ONE OR MORE CHECKS FAILED - see [FAIL] lines above
)
echo ========================================
echo.

rmdir /s /q "%MARK_DIR%" >nul 2>&1
pause
endlocal
exit /b %ANY_FAILED%

:record
REM %1 = check name, %2 = its errorlevel
if "%~2"=="0" (
    echo [PASS] %~1
) else (
    echo [FAIL] %~1
    type nul > "%MARK_DIR%\%~1.fail"
)
exit /b 0
