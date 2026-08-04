# LuckyD Browser — free local AI bootstrap (Ollama + default model).
#
# Gives every install a free, unlimited, offline AI provider with zero API
# keys: the browser's AI bridge auto-detects Ollama at 127.0.0.1:11434 and
# prefers keyless local providers above everything else (see
# browser_core/ai_bridge.py -> default_provider()).
#
# What it does:
#   1. Installs Ollama silently (per-user, no admin) when missing.
#   2. Starts the Ollama server if it isn't responding.
#   3. Pulls the default model (gemma3:4b) unless a preferred model exists.
#
# Safe to re-run: every step is skipped when already satisfied. Failures are
# soft (warnings only) so they never break the browser install itself.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ollama_setup.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File ollama_setup.ps1 -Model qwen3:8b

param(
    # default: fastest TOOL-CAPABLE model that reliably finishes agent loops
    # on CPU-only machines (tested 2026-07-28: qwen3:1.7b loops on agent
    # tasks, gemma3 has no tool support at all). Sidebar chat works with any
    # model; optional vision upgrade for it: gemma3:4b.
    [string]$Model = 'llama3.2:3b'
)

$ErrorActionPreference = 'Continue'
$OLLAMA_URL  = 'https://ollama.com/download/OllamaSetup.exe'
$OLLAMA_TAGS = 'http://127.0.0.1:11434/api/tags'
# Mirrors _LOCAL_MODEL_PREF in browser_core/ai_bridge.py — if the user already
# has any of these, we respect it and pull nothing.
$PREFERRED = @('llama3.2', 'qwen3', 'gemma3', 'phi4', 'qwen2.5', 'llama3.3', 'llama3.1', 'mistral', 'deepseek', 'gpt-oss')

function Find-Ollama {
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )) { if (Test-Path $p) { return $p } }
    return $null
}

function Test-OllamaUp {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $OLLAMA_TAGS -TimeoutSec 3
        return $r.StatusCode -eq 200
    } catch { return $false }
}

Write-Host ''
Write-Host '=== LuckyD Browser — free local AI setup ===' -ForegroundColor Cyan

# ── 1. Install Ollama if missing ────────────────────────────────────────
$ollama = Find-Ollama
if (-not $ollama) {
    Write-Host 'Ollama not found - downloading installer ...'
    $setup = Join-Path $env:TEMP 'OllamaSetup.exe'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $OLLAMA_URL -OutFile $setup
    } catch {
        Write-Host "WARNING: download failed ($($_.Exception.Message))." -ForegroundColor Yellow
        Write-Host '  Install Ollama manually from https://ollama.com/download then re-run this script.'
        exit 0
    }
    Write-Host 'Installing Ollama (silent, per-user) ...'
    Start-Process -FilePath $setup -ArgumentList '/VERYSILENT', '/NORESTART' -Wait
    # Make a just-installed Ollama visible in THIS session too.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $ollama = Find-Ollama
    if (-not $ollama) {
        Write-Host 'WARNING: Ollama installed but ollama.exe not found on PATH yet.' -ForegroundColor Yellow
        Write-Host '  Restart the PC (or sign out/in), then run: ollama pull ' $Model
        exit 0
    }
} else {
    Write-Host "Ollama found: $ollama"
}

# ── 2. Make sure the server is up ───────────────────────────────────────
if (-not (Test-OllamaUp)) {
    Write-Host 'Starting Ollama server ...'
    Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
}
$up = $false
foreach ($i in 1..15) {
    if (Test-OllamaUp) { $up = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $up) {
    Write-Host 'WARNING: Ollama server did not answer on 127.0.0.1:11434.' -ForegroundColor Yellow
    Write-Host "  After a reboot, run: ollama pull $Model"
    exit 0
}

# ── 3. Pull the default model (skip when a preferred one exists) ────────
$tags = (Invoke-WebRequest -UseBasicParsing -Uri $OLLAMA_TAGS).Content
$havePreferred = $PREFERRED | Where-Object { $tags -match [regex]::Escape($_) } | Select-Object -First 1
if ($havePreferred) {
    Write-Host "A preferred model is already installed ('$havePreferred') - nothing to pull."
} else {
    Write-Host "Pulling default model '$Model' (one-time download, a few GB) ..."
    & $ollama pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: 'ollama pull $Model' failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
        Write-Host "  Re-run manually: ollama pull $Model"
        exit 0
    }
}

Write-Host ''
Write-Host "Done! The LuckyD Browser AI assistant will now use local Ollama ($Model)" -ForegroundColor Green
Write-Host 'free, unlimited, offline - no API key needed. It is picked up automatically'
Write-Host 'on the next browser launch (Sidebar > AI provider: ollama).'
