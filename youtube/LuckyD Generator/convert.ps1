# convert.ps1
# ---------------------------------------------------------------
# ONE-CLICK Marp converter for your YouTube decks.
# Usage (from this folder):
#     right-click > Open with > PowerShell   OR
#     powershell -ExecutionPolicy Bypass -File convert.ps1 my_video.md
#
# Drop your .md files into the "decks" folder next to this script.
# Put any photos/screenshots they use into decks\images\.
#
# Exports:
#   export/<name>.html         (interactive webpage)
#
# Any words listed in decks\banned-words.txt are auto-starred out of
# the exported deck. Your original .md file is never changed.
# ---------------------------------------------------------------

param($inputFile = "my_video.md")

# This folder (works no matter where the project is moved to)
$scriptDir = $PSScriptRoot
$deckDir   = Join-Path $scriptDir "decks"
New-Item -ItemType Directory -Path $deckDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $deckDir "images") -Force | Out-Null

# Node + the Marp CLI script (same install this project's .bat files use)
$node   = "C:\Program Files\nodejs\node.exe"
$marpjs = "C:\Users\dylan\AppData\Roaming\npm\node_modules\@marp-team\marp-cli\marp-cli.js"

# All CSS files in themes\ are registered as themes; the .md file picks
# one by name via its "theme:" front-matter line.
$themeDir = Join-Path $scriptDir "themes"

if (-not (Test-Path $node)) {
    Write-Host "[!] Can't find Node.js at: $node" -ForegroundColor Red
    Write-Host "    Install it from https://nodejs.org" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $marpjs)) {
    Write-Host "[!] Can't find the Marp CLI at: $marpjs" -ForegroundColor Red
    Write-Host "    Run this once to install it:  npm install -g @marp-team/marp-cli" -ForegroundColor Red
    exit 1
}

# Resolve the input file: check as-is, then next to this script, then in decks\
if (-not (Test-Path $inputFile)) {
    $candidate = Join-Path $scriptDir $inputFile
    if (Test-Path $candidate) { $inputFile = $candidate }
}
if (-not (Test-Path $inputFile)) {
    $candidate = Join-Path $deckDir $inputFile
    if (Test-Path $candidate) { $inputFile = $candidate }
}
if (-not (Test-Path $inputFile)) {
    Write-Host "[!] Cannot find deck: $inputFile" -ForegroundColor Red
    Write-Host "    New decks go in the 'decks' folder next to this script." -ForegroundColor Red
    exit 1
}
$inputFile = (Resolve-Path $inputFile).Path

$base   = [System.IO.Path]::GetFileNameWithoutExtension($inputFile)
$srcDir = [System.IO.Path]::GetDirectoryName($inputFile)
$outDir = Join-Path $scriptDir "export"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Host "==> Exporting '$inputFile' ..." -ForegroundColor Green

# ---- auto-paginate raw chat exports that have no slide breaks, and ----
# mark every plain slide that has no picture yet with either a real
# photo from decks\images\ or an AI-image prompt tag. Already-authored
# decks (with "---" slide breaks) pass through pagination unchanged.
# Your original .md file is never modified.
$imagesDir = Join-Path $deckDir "images"
$paginated = Join-Path $srcDir "_paginated_$base.md"
& $node (Join-Path $scriptDir "autopaginate.js") $inputFile $paginated $imagesDir
if (Test-Path $paginated) { $srcFile = $paginated } else { $srcFile = $inputFile }

# ---- generate AI images for every ![gen: ...] tag (free, via Pollinations) ----
# Resolves both hand-written tags and the ones autopaginate.js just
# added into real downloaded images - saved straight into export\images\
# (next to the HTML, NOT into decks\) so decks\ stays clean and ready
# for your next project. Your original .md file is never modified.
$exportImagesDir = Join-Path $outDir "images"
New-Item -ItemType Directory -Path $exportImagesDir -Force | Out-Null
$imaged = Join-Path $srcDir "_imaged_$base.md"
& $node (Join-Path $scriptDir "genimage.js") $srcFile $imaged $exportImagesDir
if (Test-Path $imaged) { $srcFile = $imaged }

# ---- filter banned words (decks\banned-words.txt) into a temp copy ----
# The filtered copy lives next to the source file so any relative image
# paths keep working. Your original .md file is never modified.
$bannedList = Join-Path $deckDir "banned-words.txt"
$filtered   = Join-Path $srcDir "_filtered_$base.md"
& $node (Join-Path $scriptDir "filter-check.js") $srcFile $bannedList $filtered
if (Test-Path $filtered) { $srcFile = $filtered }

# ---- drop any dead links (404s, timeouts, etc.) into a temp copy ----
# A dead link becomes plain text instead of a broken clickable link.
# Your original .md file is never modified.
$linkChecked = Join-Path $srcDir "_linkchecked_$base.md"
& $node (Join-Path $scriptDir "checklinks.js") $srcFile $linkChecked
if (Test-Path $linkChecked) { $srcFile = $linkChecked }

# ---- build a video-embedded copy just for the HTML export ----
# (video only plays in HTML; pptx/pdf/png keep plain links)
$videoized = Join-Path $srcDir "_video_$base.md"
& $node (Join-Path $scriptDir "embedvideo.js") $srcFile $videoized
if (-not (Test-Path $videoized)) { $videoized = $srcFile }

# Interactive HTML (best for reviewing / screen-recording)
& $node $marpjs $videoized -o (Join-Path $outDir "$base.html") --theme-set $themeDir --allow-local-files --html

if (Test-Path $filtered) { Remove-Item $filtered -Force }
if (Test-Path $paginated) { Remove-Item $paginated -Force }
if (Test-Path $imaged) { Remove-Item $imaged -Force }
if (Test-Path $linkChecked) { Remove-Item $linkChecked -Force }
if ((Test-Path $videoized) -and ($videoized -ne $srcFile)) { Remove-Item $videoized -Force }

Write-Host ""
Write-Host "DONE! Files are in:" -ForegroundColor Green
Write-Host "  $outDir"
Start-Process $outDir
