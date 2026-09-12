# Builds the LuckyD Windows installer end-to-end:
#   1. Agents bridge sidecar (PyInstaller -> luckyd-agents-bridge.exe)
#   2. UI production build (tsc + vite)
#   3. electron-builder NSIS setup (bundles electron-main, preload, dist,
#      icon, luckyd-code.exe, and the agents bridge exe)
#   4. Copies artifacts to the repo-root "installers/" folder
# Usage:  powershell -File scripts/build-installer.ps1
#         or  npm run installer   (from apps/luckyd-ui)

$ErrorActionPreference = 'Stop'
$uiDir = Split-Path -Parent $PSScriptRoot   # apps/luckyd-ui
$repo = Split-Path -Parent (Split-Path -Parent $uiDir)  # repo root

Write-Host "== LuckyD installer build ==" -ForegroundColor Cyan
Write-Host "UI dir: $uiDir"

Push-Location $uiDir
try {
  Write-Host "`n[1/4] Building agents bridge (PyInstaller)..." -ForegroundColor Yellow
  python -m PyInstaller agents_bridge.spec --noconfirm --distpath scripts/dist --workpath scripts/build
  if ($LASTEXITCODE -ne 0) { throw "bridge build failed with exit $LASTEXITCODE" }

  Write-Host "`n[2/4] Building UI (tsc + vite)..." -ForegroundColor Yellow
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "UI build failed with exit $LASTEXITCODE" }

  Write-Host "`n[3/4] Packaging with electron-builder (NSIS)..." -ForegroundColor Yellow
  npx electron-builder --win
  if ($LASTEXITCODE -ne 0) { throw "electron-builder failed with exit $LASTEXITCODE" }
} finally {
  Pop-Location
}

Write-Host "`n[4/4] Collecting artifacts..." -ForegroundColor Yellow
$rel = Join-Path $uiDir 'release'
$out = Join-Path $repo 'installers'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$setup = Get-ChildItem $rel -Filter 'LuckyD-Setup-*.exe' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setup) {
  Copy-Item $setup.FullName (Join-Path $out $setup.Name) -Force
  Write-Host "Installer: $(Join-Path $out $setup.Name)" -ForegroundColor Green
} else {
  Write-Host "No LuckyD-Setup-*.exe found in $rel" -ForegroundColor Red
  Get-ChildItem $rel -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_.Name }
}

Write-Host "`nDone." -ForegroundColor Cyan
