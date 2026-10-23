@echo off
title Cline Auth
echo ============================================
echo   Cline Authentication - Starting
echo ============================================
echo.
echo This will open your browser for Google sign-in.
echo Log in with: dylanchess02@gmail.com
echo.
echo After signing in, come back here to see confirmation.
echo ============================================
echo.
"%USERPROFILE%\AppData\Roaming\npm\node_modules\cline\node_modules\@cline\cli-windows-x64\bin\cline.exe" auth cline
echo.
echo ============================================
echo   Auth process complete!
echo   Check the output above.
echo ============================================
pause
