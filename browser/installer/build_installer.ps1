# LuckyD Browser -- one-command release build.
#
#   powershell -NoProfile -ExecutionPolicy RemoteSigned -File browser\installer\build_installer.ps1
#
# Steps:
#   1. PyInstaller  -> repoRoot\dist\luckyd-code.exe  (headless HQ/harness
#                      backend, built from web_server.py). The packaged
#                      browser launches this exe for the /hq iframe tab
#                      (browser_core/harness_bridge.py._find_exe()).
#   2. PyInstaller  -> repoRoot\dist\luckyd-cli.exe  (the REAL interactive
#                      terminal CLI, built from main.py). The packaged
#                      browser's Terminal tab "agent" shell spawns THIS one
#                      (browser_core/terminal_server.py._cli_command()) --
#                      it's the only one of the two with a stdin prompt loop,
#                      /tools, /help, /model, and every registered tool,
#                      including the multi-agent "mesh" tools (AgentHandoff,
#                      TeamCreate, SendMessage, ReceiveMessage, ListAgents,
#                      SubAgent).
#   Both #1 and #2 are rebuilt FIRST and from CURRENT source every time --
#   the browser's own PyInstaller step (#3) separately bundles fresh copies
#   of core/, tools/, etc. as inert data files that are never executed
#   directly; if these two backend exes are stale, every agent feature added
#   since their last build stays invisible in the shipped app no matter how
#   fresh the browser build itself is.
#   3. PyInstaller  -> browser\dist\LuckyDBrowser\  (the browser app itself,
#                      bundles the two freshly rebuilt exes above)
#   4. Inno Setup   -> browser\installer\output\LuckyDBrowserSetup-X.Y.Z.exe
#                      (the shareable installer anyone can run)
#
# Requires: Python 3.10-3.12 with PyInstaller, and Inno Setup 6
# (download: https://jrsoftware.org/isdl.php -- a per-user install is fine).

$ErrorActionPreference = 'Stop'
$repoRoot    = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$browserDir  = Join-Path $repoRoot 'browser'
$issScript   = Join-Path $PSScriptRoot 'LuckyDBrowser.iss'

function Write-Step($msg) { Write-Host '' ; Write-Host "== $msg ==" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK: $msg" -ForegroundColor Green }

# Rebuild one repo-root backend exe from its spec and refresh the repo-root
# copy that LuckyDBrowser.spec bundles. Kills any process by that name first
# (it may still be running, spawned earlier by harness_bridge.py or
# terminal_server.py, and holding its own exe file open) and retries the
# overwrite briefly in case the OS hasn't released the handle yet.
function Rebuild-BackendExe([string]$SpecFile, [string]$ExeName) {
    Push-Location $repoRoot
    try {
        python -m PyInstaller --noconfirm --clean $SpecFile
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed on $SpecFile (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }

    $fresh = Join-Path $repoRoot "dist\$ExeName"
    if (-not (Test-Path $fresh)) { throw "Build output missing: $fresh" }

    $procName = [System.IO.Path]::GetFileNameWithoutExtension($ExeName)
    Get-Process -Name $procName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    $dest = Join-Path $repoRoot $ExeName
    $copied = $false
    for ($i = 0; $i -lt 5; $i++) {
        try {
            Copy-Item $fresh -Destination $dest -Force
            $copied = $true
            break
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }
    if (-not $copied) {
        throw "Could not overwrite $ExeName -- it's still locked by a running process. Close it manually (check Task Manager for $ExeName) and re-run."
    }
    Write-Ok "$ExeName rebuilt and refreshed at repo root ($('{0:N1}' -f ((Get-Item $fresh).Length / 1MB)) MB)"
}

# -- 1. Rebuild the headless HQ/harness backend from current source --------
Write-Step '[1/7] Rebuilding luckyd-code.exe (HQ/harness backend) from current source'
Rebuild-BackendExe -SpecFile 'luckyd-code.spec' -ExeName 'luckyd-code.exe'

# -- 2. Rebuild the interactive terminal CLI from current source -----------
Write-Step '[2/7] Rebuilding luckyd-cli.exe (interactive terminal CLI) from current source'
Rebuild-BackendExe -SpecFile 'main.spec' -ExeName 'luckyd-cli.exe'

# -- 3. PyInstaller (browser) ------------------------------------------------
# LuckyDBrowser.spec bundles installer/env/.env (the clean template, never a
# dev .env with real keys). Fresh checkouts don't have it (gitignored), so
# seed it from the repo-root .env.example before PyInstaller runs.
$envDir = Join-Path $PSScriptRoot 'env'
$envFile = Join-Path $envDir '.env'
if (-not (Test-Path $envFile)) {
    $template = Join-Path $repoRoot '.env.example'
    if (-not (Test-Path $template)) { throw "Missing .env template: $template" }
    New-Item -ItemType Directory -Path $envDir -Force | Out-Null
    Copy-Item $template $envFile -Force
    Write-Ok "Seeded installer/env/.env from .env.example (clean template, no keys)"
}
Write-Step '[3/7] Building LuckyDBrowser with PyInstaller'
Push-Location $browserDir
try {
    python -m PyInstaller --noconfirm --clean LuckyDBrowser.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$exe = Join-Path $browserDir 'dist\LuckyDBrowser\LuckyDBrowser.exe'
if (-not (Test-Path $exe)) { throw "Build output missing: $exe" }
Write-Ok 'LuckyDBrowser.exe built (bundles the freshly rebuilt luckyd-code.exe + luckyd-cli.exe above)'

# -- 3b. Authenticode-sign the app binaries (before Inno packs them) --------
# 9.9: signed binaries are the #1 lever against Defender/SmartScreen,
# Halcyon, and IT allowlists. No cert configured -> clean no-op.
Write-Step '[4/7] Authenticode-signing app binaries (skips cleanly without a cert)'
& "$PSScriptRoot\sign_binaries.ps1" -TargetDir (Join-Path $browserDir 'dist\LuckyDBrowser')
if ($LASTEXITCODE -ne 0) { throw "sign_binaries.ps1 failed (exit $LASTEXITCODE)" }

# -- 4. Inno Setup ------------------------------------------------------------
Write-Step '[5/7] Compiling installer with Inno Setup'
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

$setup = Get-ChildItem (Join-Path $PSScriptRoot 'output\*.exe') |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Write-Host ''
Write-Host "Done! Shareable installer: $($setup.FullName)" -ForegroundColor Green
Write-Host ('Size: {0:N1} MB' -f ($setup.Length / 1MB))

# -- 5b. Sign the installer itself (no cert -> clean no-op) -------------------
Write-Step '[6/7] Authenticode-signing the installer'
& "$PSScriptRoot\sign_binaries.ps1" -File $setup.FullName
if ($LASTEXITCODE -ne 0) { throw "sign_binaries.ps1 failed on installer (exit $LASTEXITCODE)" }

# -- 6. Portable ZIP (no installer -- for locked-down / IT-managed PCs) -------
# 9.9: many work computers block installers outright but allow running
# programs from the user's own folders. The portable ZIP is the same
# signed app binaries, just zipped: extract anywhere and run
# LuckyDBrowser\LuckyDBrowser.exe. No registry, no admin, no installer
# heuristics for Halcyon/Defender to trip on.
Write-Step '[7/7] Building portable ZIP'
$issText = Get-Content $issScript -Raw
$ver = if ($issText -match '#define AppVersion\s+"([^"]+)"') { $Matches[1] } else { throw 'Could not read AppVersion from LuckyDBrowser.iss' }
$portableZip = Join-Path $PSScriptRoot ("output\LuckyDBrowser-Portable-{0}.zip" -f $ver)
if (Test-Path $portableZip) { Remove-Item $portableZip -Force }
Compress-Archive -Path (Join-Path $browserDir 'dist\LuckyDBrowser') -DestinationPath $portableZip -CompressionLevel Optimal
Write-Ok ("Portable ZIP: $portableZip ({0:N1} MB)" -f ((Get-Item $portableZip).Length / 1MB))
