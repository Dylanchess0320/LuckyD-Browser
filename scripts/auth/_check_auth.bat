@echo off
echo ============================================
echo    Checking Cline Auth Options
echo ============================================
echo.
"C:\Users\dylan\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe" auth --help
echo.
echo ---
echo.
echo Also checking 'cline' directly:
where cline
cline auth --help
echo.
pause
