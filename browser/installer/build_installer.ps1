# LuckyD Browser — one-command release build.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File browser\installer\build_installer.ps1
#
# Steps:
#   1. PyInstaller  -> browser\dist\LuckyDBrowser\  (the app itself)
#   2. Inno Setup   -> browser\installer\output\LuckyDBrowserSetup-2.2.0.exe
#                      (the shareable installer anyone can run)
#
# Requires: Python 3.10-3.12 with PyInstaller, and Inno Setup 6
# (download: https://jrsoftware.org/isdl.php — a per-user install is fine).

$ErrorActionPreference = 'Stop'
$repoRoot    = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$browserDir  = Join-Path $repoRoot 'browser'
$issScript   = Join-Path $PSScriptRoot 'LuckyDBrowser.iss'

# ── 1. PyInstaller ────────────────────────────────────────────────────
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

# ── 2. Inno Setup ─────────────────────────────────────────────────────
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

& $iscc $issScript
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed (exit $LASTEXITCODE)" }

$setup = Get-ChildItem (Join-Path $PSScriptRoot 'output\*.exe') | Select-Object -First 1
Write-Host ''
Write-Host "Done! Shareable installer: $($setup.FullName)" -ForegroundColor Green
Write-Host ('Size: {0:N1} MB' -f ($setup.Length / 1MB))
