# LuckyD Browser - one-command release build.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
#
# Steps:
#   1. PyInstaller  -> browser\dist\LuckyDBrowser\  (the app itself)
#   2. Inno Setup   -> browser\installer\output\LuckyDBrowserSetup-<version>.exe
#                      (the shareable installer anyone can run - version comes
#                      from AppVersion in LuckyDBrowser.iss, so this comment is
#                      never hardcoded and can't go stale again)
#
# Requires: Python 3.10-3.12 with PyInstaller, and Inno Setup 6
# (download: https://jrsoftware.org/isdl.php - a per-user install is fine).

$ErrorActionPreference = 'Stop'
$repoRoot    = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$browserDir  = Join-Path $repoRoot 'browser'
$issScript   = Join-Path $PSScriptRoot 'LuckyDBrowser.iss'
$outputDir   = Join-Path $PSScriptRoot 'output'

# -- 1. PyInstaller -------------------------------------------------------
Write-Host '[1/2] Building LuckyDBrowser with PyInstaller ...' -ForegroundColor Cyan
Push-Location $browserDir
try {
    python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$exe = Join-Path $browserDir 'dist\LuckyDBrowser\LuckyDBrowser.exe'
if (-not (Test-Path $exe)) { throw "Build output missing: $exe" }

# -- 2. Inno Setup ----------------------------------------------------------
Write-Host '[2/2] Compiling installer with Inno Setup ...' -ForegroundColor Cyan
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $iscc) {
    throw 'Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php'
}

# Snapshot what's already in output\ *before* compiling, so we can identify
# the file this run actually produced instead of guessing.
$before = @{}
if (Test-Path $outputDir) {
    Get-ChildItem $outputDir -Filter '*.exe' -File | ForEach-Object { $before[$_.FullName] = $true }
}

& $iscc $issScript
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed (exit $LASTEXITCODE)" }

# BUG FIX: this used to be
#   Get-ChildItem (Join-Path $PSScriptRoot 'output\*.exe') | Select-Object -First 1
# which picks the alphabetically-first .exe in the folder -- so an old build
# left sitting in output\ (e.g. "...-1.3.0.exe") would get reported as "Done!"
# instead of the installer just compiled. Prefer the file that's new this run;
# fall back to most-recently-written if the snapshot comparison finds nothing
# (e.g. a fresh/empty output\ folder).
$candidates = Get-ChildItem $outputDir -Filter '*.exe' -File
$setup = $candidates | Where-Object { -not $before.ContainsKey($_.FullName) } | Select-Object -First 1
if (-not $setup) {
    $setup = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $setup) { throw "No installer .exe found in $outputDir after compile" }

Write-Host ''
Write-Host "Done! Shareable installer: $($setup.FullName)" -ForegroundColor Green
Write-Host ('Size: {0:N1} MB' -f ($setup.Length / 1MB))

# Clean up old_builds\ noise, if any, so `output\` always shows only the
# current installer at a glance.
$oldBuilds = Join-Path $outputDir 'old_builds'
if (Test-Path $oldBuilds) {
    Write-Host "Note: older installer(s) are archived in $oldBuilds - safe to delete manually." -ForegroundColor DarkGray
}
