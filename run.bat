@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: ── Console setup ─────────────────────────────────────────────────────
:: Set UTF-8 codepage so box-drawing chars and emoji render correctly
chcp 65001 >nul 2>&1
:: Enable ANSI/VT escape processing for this console window (Win10+)
:: (ui.py also does this via ctypes, but doing it here covers any
::  output produced before Python starts, e.g. error messages)
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"

title LuckyD Code

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
    echo %ESC%[91mError: Python not found.%ESC%[0m
    echo.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: ── Dependency check ──────────────────────────────────────────────────
:: Quick check: can we import the key deps? If not, offer to install.
%PYTHON% -c "import rich, httpx" >nul 2>&1
if errorlevel 1 (
    echo %ESC%[93mDependencies missing. Installing from requirements.txt...%ESC%[0m
    echo.
    if not exist "requirements.txt" (
        echo %ESC%[91mError: requirements.txt not found.%ESC%[0m
        pause
        exit /b 1
    )
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo %ESC%[91mDependency install failed. See errors above.%ESC%[0m
        pause
        exit /b 1
    )
    echo.
    echo %ESC%[92mDependencies installed.%ESC%[0m
    echo.
)

:: ── Run ───────────────────────────────────────────────────────────────
%PYTHON% main.py %*
set "EXIT_CODE=%errorlevel%"

:: ── Exit handling ─────────────────────────────────────────────────────
:: If the app crashed (non-zero exit), keep the window open so the
:: user can read the traceback instead of it vanishing instantly.
if not "%EXIT_CODE%"=="0" (
    echo.
    echo %ESC%[91mLuckyD Code exited with code %EXIT_CODE%.%ESC%[0m
    pause
)

endlocal
exit /b %EXIT_CODE%
