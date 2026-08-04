# LuckyD Browser - per-user installer (no admin required).
# Copies the packaged app to %LOCALAPPDATA%\Programs, creates Start Menu +
# Desktop shortcuts, and registers it in Settings > Apps with an uninstaller.
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File browser\install.ps1

$ErrorActionPreference = 'Stop'
$appName = 'LuckyD Browser'
$exeName = 'LuckyDBrowser.exe'
$src = Join-Path $PSScriptRoot 'dist\LuckyDBrowser'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\LuckyDBrowser'
$desktop = [Environment]::GetFolderPath('Desktop')
$startMenuPrograms = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'

if (-not (Test-Path (Join-Path $src $exeName))) {
    throw "Build not found at $src - build the exe with PyInstaller first."
}

Write-Host "Installing $appName to $dest ..."
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Copy-Item $src $dest -Recurse

# --- Shortcuts ---
$shell = New-Object -ComObject WScript.Shell
$shortcutPaths = @(
    (Join-Path $startMenuPrograms 'LuckyD Browser.lnk'),
    (Join-Path $desktop 'LuckyD Browser.lnk')
)
foreach ($lnk in $shortcutPaths) {
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $dest $exeName
    $sc.WorkingDirectory = $dest
    $sc.IconLocation = Join-Path $dest $exeName
    $sc.Description = 'LuckyD Browser - Chromium-based web browser by LuckyD'
    $sc.Save()
    Write-Host "  shortcut: $lnk"
}

# --- Settings > Apps entry (per-user uninstall registry) ---
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LuckyDBrowser'
$uninstallScript = Join-Path $dest 'uninstall.ps1'
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty $regPath 'DisplayName' $appName
Set-ItemProperty $regPath 'DisplayVersion' '1.3.0'
Set-ItemProperty $regPath 'Publisher' 'LuckyD'
Set-ItemProperty $regPath 'InstallLocation' $dest
Set-ItemProperty $regPath 'DisplayIcon' (Join-Path $dest $exeName)
Set-ItemProperty $regPath 'UninstallString' "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallScript`""
Set-ItemProperty $regPath 'NoModify' 1
Set-ItemProperty $regPath 'NoRepair' 1

# --- Uninstaller ---
@'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\LuckyDBrowser'
Get-Process LuckyDBrowser -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process QtWebEngineProcess -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ([Environment]::GetFolderPath('Desktop') + '\LuckyD Browser.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item ((Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs') + '\LuckyD Browser.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LuckyDBrowser' -Recurse -Force -ErrorAction SilentlyContinue
# Browsing data (history, bookmarks, settings) is KEPT at %LOCALAPPDATA%\LuckyDBrowser
# - delete that folder manually if you want a full wipe.
# Ollama (the free local AI runtime) is a separate product and is LEFT installed
# - remove it via Settings > Apps > Ollama if you don't want it.
Write-Host 'LuckyD Browser uninstalled. (Browsing data kept in %LOCALAPPDATA%\LuckyDBrowser)'
'@ | Set-Content $uninstallScript -Encoding UTF8

# --- Free local AI (Ollama) --------------------------------------------
# Installs Ollama if missing and pulls the default model, so every user gets
# a free, unlimited, keyless AI provider out of the box. Soft-fails with
# warnings — never blocks the browser install itself.
$aiSetup = Join-Path $PSScriptRoot 'installer\ollama_setup.ps1'
if (Test-Path $aiSetup) { & $aiSetup }

Write-Host ''
Write-Host "$appName installed successfully!"
Write-Host '  Launch: Start Menu or the desktop shortcut'
Write-Host "  Uninstall: Settings > Apps > $appName"
Write-Host '  Browsing data: %LOCALAPPDATA%\LuckyDBrowser'

