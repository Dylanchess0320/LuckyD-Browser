@echo off
cd /d C:\Users\dylan\OneDrive\Desktop\coding-agent
echo === RUFF CHECK ===
python -m ruff check . --statistics 2>&1
echo.
echo === RUFF FORMAT CHECK ===
python -m ruff format --check . 2>&1
echo.
echo === MYPY ===
python -m mypy . --ignore-missing-imports --no-error-summary 2>&1
