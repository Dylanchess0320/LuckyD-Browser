@echo off
cd /d "%~dp0"
"C:\Users\dylan\AppData\Local\Programs\Inno Setup 6\ISCC.exe" "installer\LuckyDBrowser.iss" > installer\iscc_log.txt 2>&1
