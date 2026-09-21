# LuckyD Browser -- Authenticode signing helper (9.9+).
#
# Signs binaries with the project's code-signing certificate so Windows
# Defender/SmartScreen, Halcyon, and enterprise IT policies see a known
# publisher instead of "unknown".
#
# The certificate is NEVER stored in the repo. Provide it via env vars:
#   LUCKYD_CERT_PFX_B64  - base64-encoded .pfx/.p12 code-signing certificate
#   LUCKYD_CERT_PASSWORD - password for the PFX (may be empty)
#
# Behavior:
#   - Cert env vars absent -> prints a notice, exits 0 (unsigned build,
#     everything works exactly as before).
#   - Cert present but signtool.exe missing -> THROWS. You asked for
#     signing, so a missing signer is a build error, not a silent skip.
#
# CI wiring: .github/workflows/windows-installer.yml maps the
# LUCKYD_CERT_PFX_B64 / LUCKYD_CERT_PASSWORD GitHub Secrets onto these
# env vars. Until those secrets exist, CI builds stay unsigned.

param(
    [string]$TargetDir = "",
    [string]$File = ""
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host '' ; Write-Host "== $msg ==" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK: $msg" -ForegroundColor Green }

$pfxB64 = $env:LUCKYD_CERT_PFX_B64
if ([string]::IsNullOrWhiteSpace($pfxB64)) {
    Write-Host '  (no LUCKYD_CERT_PFX_B64 set -- skipping Authenticode signing, binaries stay unsigned)'
    exit 0
}

# Locate signtool.exe (Windows SDK / VS Build Tools / PATH).
$signtool = @(
    (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source,
    "$env:ProgramFiles(x86)\Windows Kits\10\bin\x64\signtool.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $signtool) {
    $sdkBin = "$env:ProgramFiles(x86)\Windows Kits\10\bin"
    if (Test-Path $sdkBin) {
        $signtool = Get-ChildItem "$sdkBin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $signtool) {
    throw 'LUCKYD_CERT_PFX_B64 is set but signtool.exe was not found. Install the Windows SDK (or Visual Studio Build Tools) on this machine.'
}

if ($File -ne "") {
    $files = @(Get-Item $File)
} elseif ($TargetDir -ne "") {
    $files = @(Get-ChildItem -Path $TargetDir -Include *.exe, *.dll -Recurse -File)
} else {
    throw 'sign_binaries.ps1 needs -TargetDir or -File.'
}
if ($files.Count -eq 0) { Write-Host '  (nothing to sign)'; exit 0 }

$pfxPath = Join-Path ([System.IO.Path]::GetTempPath()) ('luckyd-cert-{0}.pfx' -f ([guid]::NewGuid().ToString('N')))
try {
    [System.IO.File]::WriteAllBytes($pfxPath, [System.Convert]::FromBase64String($pfxB64))
    $signArgs = @('sign', '/fd', 'SHA256', '/f', $pfxPath)
    if (-not [string]::IsNullOrEmpty($env:LUCKYD_CERT_PASSWORD)) {
        $signArgs += @('/p', $env:LUCKYD_CERT_PASSWORD)
    }
    $signArgs += @('/tr', 'http://timestamp.digicert.com', '/td', 'SHA256')
    Write-Step "Signing $($files.Count) file(s) with Authenticode"
    foreach ($f in $files) {
        & $signtool @signArgs $f.FullName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "signtool failed on $($f.FullName) (exit $LASTEXITCODE)" }
    }
    Write-Ok "Signed $($files.Count) file(s)"
} finally {
    if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
}
