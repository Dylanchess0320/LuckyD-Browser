@echo off
REM ============================================================
REM   YOUTUBE DECK GENERATOR  (Marp, black + green theme)  v4
REM ============================================================
REM  Turns ANY Markdown (.md) file into a presentation:
REM      export\<name>.html         -> interactive slides (open in any browser)
REM
REM  Drop your .md files into the "decks" folder next to this .bat.
REM  Put any photos/screenshots they use into decks\images\.
REM
REM  HOW TO FEED IT A FILE - 4 ways:
REM    1) Double-click this .bat, then type the FILE NAME (no .md needed)
REM    2) Double-click, then type a NUMBER from the list shown
REM    3) DRAG a .md file onto this .bat (works from any folder)
REM    4) Type A to convert every .md file in the decks folder in one go
REM
REM  Other commands at the menu:
REM    N = start a new deck (copies my_video.md as a template into decks\)
REM    O = open the export folder
REM    Q = quit
REM
REM  Any words listed in decks\banned-words.txt get auto-starred out of
REM  the exported deck. Your original .md file is never changed.
REM
REM  OVERFLOW GUARANTEE: after building, a real headless browser checks
REM  every slide's actual rendered height. Anything that doesn't fit
REM  gets auto-shrunk (then auto-split if still too tall) and re-checked
REM  until it verifiably fits. One-time setup: run "npm install" in this
REM  folder to enable it (decks still build fine without it - they just
REM  skip the real-browser check).
REM ============================================================

setlocal enabledelayedexpansion

REM ---- config (edit these if paths change) ----
set "node=C:\Program Files\nodejs\node.exe"
REM Node.js script that actually runs Marp (skips the .cmd shim):
set "marpjs=C:\Users\dylan\AppData\Roaming\npm\node_modules\@marp-team\marp-cli\marp-cli.js"
REM All CSS files in this folder are registered as themes; each .md file
REM picks one by name via its "theme:" front-matter line:
set "themedir=%~dp0themes"
REM Folder where your .md decks (and decks\images\) live:
set "deckdir=%~dp0decks"
REM ---------------------------------------------

color 0A
title YouTube Deck Generator (black + green)

REM ---- sanity check: make sure Node + Marp CLI are actually installed ----
if not exist "%node%" (
  echo.
  echo   [!] Can't find Node.js at:
  echo       %node%
  echo.
  echo       Install it from https://nodejs.org, or edit the "node" line
  echo       near the top of generator.bat if it's installed somewhere else.
  echo.
  pause
  exit /b 1
)
if not exist "%marpjs%" (
  echo.
  echo   [!] Can't find the Marp CLI at:
  echo       %marpjs%
  echo.
  echo       Run this once, in any terminal, to install it:
  echo         npm install -g @marp-team/marp-cli
  echo       Then re-open this tool.
  echo.
  pause
  exit /b 1
)
if not exist "%deckdir%" mkdir "%deckdir%"
if not exist "%deckdir%\images" mkdir "%deckdir%\images"

if not exist "%~dp0node_modules\puppeteer" (
  echo.
  echo   [i] One-time setup available: run "npm install" in this folder to
  echo       turn on the real-browser overflow checker ^(catches anything
  echo       that would still get clipped, and auto-fixes it^). Decks will
  echo       build fine without it - just skips that extra safety check.
  echo.
)

if not "%~1"=="" (
  call :convert "%~1"
  call :openexport
  exit /b
)

:menu
cls
echo.
echo   + - - - - - - - - - - - - - - - - - - - - - - - - - +
echo   ^|          YOUTUBE DECK GENERATOR  (black+green)     ^|
echo   + - - - - - - - - - - - - - - - - - - - - - - - - - +
echo.
echo   Markdown decks found in:  decks\
echo.
set c=0
for %%f in ("%deckdir%\*.md") do (
  set /a c+=1
  set "f!c!=%%f"
  echo      [!c!]  %%~nxf
)
if "!c!"=="0" (
  echo      [none yet - type N to create your first one]
)
echo.
echo   Commands:   A = convert ALL decks       N = start a new deck
echo               O = open export folder      Q = quit
echo.
set "in="
set /p "in=   Type a filename, a number above, or a command, then ENTER:  "
if not defined in goto :menu

REM ---- commands ----
if /i "!in!"=="Q" exit /b
if /i "!in!"=="O" (
  call :openexport
  goto :menu
)
if /i "!in!"=="N" (
  call :newdeck
  goto :menu
)
if /i "!in!"=="A" (
  if "!c!"=="0" (
    echo.
    echo   [nothing to convert yet - type N to create a deck]
    echo.
    pause
    goto :menu
  )
  for /l %%i in (1,1,!c!) do call :convert "!f%%i!"
  call :openexport
  goto :menu
)

set "chosen="

REM ---- try it as a filename first (inside decks\) ----
if exist "%deckdir%\!in!"      set "chosen=%deckdir%\!in!"
if exist "%deckdir%\!in!.md"   set "chosen=%deckdir%\!in!.md"
if defined chosen (
  call :convert "!chosen!"
  call :openexport
  goto :menu
)

REM ---- otherwise try it as a menu number ----
set "isnum="
for /f "delims=0123456789" %%d in ("!in!") do set "isnum=1"
if not defined isnum call set "chosen=%%f!in!%%"

if not defined chosen (
  echo.
  echo   [!] Not found:
  echo       no file named "!in!" in decks\ and no #!in! in the list.
  echo       Press any key to go back...
  pause >nul
  goto :menu
)

call :convert "!chosen!"
call :openexport
goto :menu

REM ============================================================
:newdeck
echo.
set /p "name=   Name for the new deck (no .md, e.g. third_video):  "
if not defined name goto :eof
set "newfile=%deckdir%\!name!.md"
if exist "!newfile!" (
  echo.
  echo   [!] That name is already used:
  echo       "!name!.md" already exists in decks\ - pick a different name.
  echo.
  pause
  goto :eof
)
if not exist "%deckdir%\my_video.md" (
  echo.
  echo   [!] Can't find decks\my_video.md to use as a template.
  echo.
  pause
  goto :eof
)
copy /y "%deckdir%\my_video.md" "!newfile!" >nul
echo.
echo   Created decks\!name!.md from the my_video.md template.
echo   Opening it in Notepad so you can edit the title and slides...
start "" notepad "!newfile!"
goto :eof

REM ============================================================
:openexport
if not exist "%~dp0export" mkdir "%~dp0export"
start "" "%~dp0export"
goto :eof

REM ============================================================
:convert
REM ---- %1 = full path to the markdown file ----
if not exist "%~1" (
  echo.
  echo   [!] Cannot find this file - check the path/name:
  echo       %~1
  echo.
  pause
  goto :done
)
set "base=%~n1"
set "outdir=%~dp0export"
if not exist "%outdir%" mkdir "%outdir%"

REM ---- remove older export so Marp always writes a fresh file ----
if exist "%outdir%\%base%.html" del "%outdir%\%base%.html"

cls
echo.
echo   Converting:  %~n1.md
echo        to      %outdir%\
echo.

REM ---- build.js runs the whole pipeline (paginate, AI images, word ----
REM      filter, video embed, Marp render), then verifies the result in
REM      a real headless browser and auto-repairs anything that doesn't
REM      fit before finishing. Your original .md is never touched.
call "%node%" "%~dp0build.js" "%~1" "%outdir%\%base%.html" "%deckdir%\images" "%deckdir%\banned-words.txt" "%themedir%" "%node%" "%marpjs%"

echo.
echo   DONE! Your deck is in the export\ folder:
echo   ---------------------------------------------------------
if exist "%outdir%\%base%.html" echo    [ok]  %base%.html
if not exist "%outdir%\%base%.html" (
  echo    [!] Nothing was created - scroll up to see the error above.
)
echo   ---------------------------------------------------------
echo.
pause
:done
exit /b
