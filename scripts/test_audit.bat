@echo off
cd /d C:\Users\dylan\OneDrive\Desktop\coding-agent
echo === PYTEST ===
python -m pytest tests/ -v --tb=short 2>&1
