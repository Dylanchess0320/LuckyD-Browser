@echo off
start "Cline Auth" cmd /K "title Cline Auth && echo Starting Cline auth... && \"%USERPROFILE%\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe\" auth cline"
