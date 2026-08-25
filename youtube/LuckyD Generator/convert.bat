@echo off
REM ============================================================
REM  ONE-CLICK converter for your YouTube decks.
REM  Double-click this file. It turns the .md deck into:
REM    export\<name>.html         (interactive slides - open in any browser)
REM
REM  Drop your .md files into the "decks" folder next to this .bat.
REM  Put any photos/screenshots they use into decks\images\.
REM
REM  Optional: drag a .md file onto this .bat to convert THAT file
REM  (works even if it lives in a different folder).
REM  For a menu with more options (convert-all, new deck, etc.),
REM  use generator.bat instead.
REM
REM  Any words listed in decks\banned-words.txt are auto-starred
REM  out of the exported deck. Your original .md is never changed.
REM ============================================================

set "node=C:\Program Files\nodejs\node.exe"
set "marpjs=C:\Users\dylan\AppData\Roaming\npm\node_modules\@marp-team\marp-cli\marp-cli.js"
REM All CSS files in this folder are registered as themes; the .md file
REM picks one by name via its "theme:" front-matter line:
set "themedir=%~dp0themes"
set "deckdir=%~dp0decks"

if not exist "%node%" (
  echo.
  echo   [!] Can't find Node.js at: %node%
  echo       Install it from https://nodejs.org, or edit the "node" line
  echo       near the top of this .bat if it's installed somewhere else.
  echo.
  pause
  exit /b 1
)
if not exist "%marpjs%" (
  echo.
  echo   [!] Can't find the Marp CLI at: %marpjs%
  echo       Run this once to install it:  npm install -g @marp-team/marp-cli
  echo.
  pause
  exit /b 1
)
if not exist "%deckdir%" mkdir "%deckdir%"
if not exist "%deckdir%\images" mkdir "%deckdir%\images"

REM ---- default deck, or whatever was dragged onto this .bat ----
set "label=my_video.md"
set "base=my_video"
set "srcfile=%deckdir%\my_video.md"
if not "%~1"=="" (
  set "label=%~nx1"
  set "base=%~n1"
  set "srcfile=%~f1"
)

set "dir=%~dp0"
if not exist "%dir%export" mkdir "%dir%export"

if not exist "%srcfile%" (
  echo.
  echo   [!] Cannot find "%srcfile%" - check the file name / path.
  echo       New decks go in the "decks" folder next to this .bat.
  echo.
  pause
  exit /b 1
)

echo.
echo   Converting:  %label%
echo   Output to:   %dir%export\
echo.

REM ---- auto-paginate raw chat exports that have no slide breaks, and ----
REM      mark every plain slide that has no picture yet with either a
REM      real photo from decks\images\ or an AI-image prompt tag; your
REM      original .md is never touched.
for %%A in ("%srcfile%") do set "paginated=%%~dpA_paginated_%base%.md"
call "%node%" "%~dp0autopaginate.js" "%srcfile%" "%paginated%" "%deckdir%\images"
if exist "%paginated%" set "srcfile=%paginated%"

REM ---- generate AI images for every ![gen: ...] tag (free, via Pollinations) ----
REM      resolves both hand-written tags and the ones autopaginate.js
REM      just added into real downloaded images - saved straight into
REM      export\images\ (next to the HTML, NOT into decks\) so decks\
REM      stays clean and ready for your next project; your original
REM      .md is never touched.
for %%A in ("%srcfile%") do set "imaged=%%~dpA_imaged_%base%.md"
call "%node%" "%~dp0genimage.js" "%srcfile%" "%imaged%" "%dir%export\images"
if exist "%imaged%" set "srcfile=%imaged%"

REM ---- filter banned words (decks\banned-words.txt) into a temp copy ----
REM      the filtered copy lives next to the source file so any relative
REM      image paths keep working; your original .md is never touched.
for %%A in ("%srcfile%") do set "filtered=%%~dpA_filtered_%base%.md"
call "%node%" "%~dp0filter-check.js" "%srcfile%" "%deckdir%\banned-words.txt" "%filtered%"
if exist "%filtered%" set "srcfile=%filtered%"

REM ---- drop any dead links (404s, timeouts, etc.) into a temp copy ----
REM      a dead link becomes plain text instead of a broken clickable
REM      link; your original .md is never touched.
for %%A in ("%srcfile%") do set "linkchecked=%%~dpA_linkchecked_%base%.md"
call "%node%" "%~dp0checklinks.js" "%srcfile%" "%linkchecked%"
if exist "%linkchecked%" set "srcfile=%linkchecked%"

REM ---- build a video-embedded copy just for the HTML export ----
REM      (video only plays in HTML; pptx/pdf/png keep plain links)
for %%A in ("%srcfile%") do set "videoized=%%~dpA_video_%base%.md"
call "%node%" "%~dp0embedvideo.js" "%srcfile%" "%videoized%"
if not exist "%videoized%" set "videoized=%srcfile%"

echo  ...this can take ~10-20 seconds...

call "%node%" "%marpjs%" "%videoized%" -o "%dir%export\%base%.html"  --theme-set "%themedir%" --allow-local-files --html

if exist "%filtered%" del "%filtered%"
if exist "%paginated%" del "%paginated%"
if exist "%imaged%" del "%imaged%"
if exist "%linkchecked%" del "%linkchecked%"
if exist "%videoized%" if not "%videoized%"=="%srcfile%" del "%videoized%"

echo.
echo  DONE! Files are in:  %dir%export\
echo.
start "" "%dir%export"
pause
