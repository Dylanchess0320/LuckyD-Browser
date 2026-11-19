#requires -Version 5.1
<#
.SYNOPSIS
    LuckyD Browser agent setup bootstrap for Windows 10/11 x64.

.DESCRIPTION
    Installs the prerequisites and optional Agent Mesh CLIs used by LuckyD
    Browser's terminal. The script is idempotent: tools that are already
    installed are skipped. It never writes API keys or tokens.

    Groups:
      core    Node.js LTS, Git, GitHub CLI, Python
      ollama  Ollama plus a tool-capable local model
      free    Free Agent Mesh CLIs (Qwen, OpenClaw, Cline, Pi, Jules, Hermes)
      keyed   Keyed Agent Mesh CLIs (Claude, Codex, OpenCode, MiniMax, Grok, Aider)
      wsl     WSL2/Ubuntu checks and Muse instructions
      all     core + ollama + free + keyed + wsl

.PARAMETER Groups
    One or more groups to install, comma-separated. Default: core, ollama.
    Valid groups: core, ollama, free, keyed, wsl, all.

.PARAMETER OllamaModel
    Model to pull when Ollama is set up. Default: llama3.2:3b.

.PARAMETER DryRun
    Print the actions without running them.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\LuckyD-Bootstrap.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\LuckyD-Bootstrap.ps1 -Groups all -OllamaModel qwen3:8b

.NOTES
    Read LuckyD-Browser-Setup-Guide.md before running. Review the commands.
#>

[CmdletBinding()]
param(
    [string[]]$Groups = @('core', 'ollama'),

    [string]$OllamaModel = 'llama3.2:3b',

    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Warnings = New-Object System.Collections.Generic.List[string]

function Write-Head {
    param([string]$Text)
    Write-Host ''
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host "  [ok] $Text" -ForegroundColor Green
}

function Write-Skip {
    param([string]$Text)
    Write-Host "  [skip] $Text" -ForegroundColor DarkGray
}

function Write-Note {
    param([string]$Text)
    Write-Host "  [note] $Text" -ForegroundColor Yellow
}

function Write-Warn {
    param([string]$Text)
    Write-Host "  [warn] $Text" -ForegroundColor Yellow
    $script:Warnings.Add($Text)
}

function Write-Bad {
    param([string]$Text)
    Write-Host "  [fail] $Text" -ForegroundColor Red
    $script:Failures.Add($Text)
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    $parts = New-Object System.Collections.Generic.List[string]
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($machinePath) {
        foreach ($entry in $machinePath.Split(';')) {
            if ($entry -and -not $parts.Contains($entry)) { $parts.Add($entry) }
        }
    }
    if ($userPath) {
        foreach ($entry in $userPath.Split(';')) {
            if ($entry -and -not $parts.Contains($entry)) { $parts.Add($entry) }
        }
    }
    $npmBin = Join-Path $env:APPDATA 'npm'
    if ((Test-Path -LiteralPath $npmBin) -and -not $parts.Contains($npmBin)) {
        $parts.Add($npmBin)
    }
    $env:Path = ($parts -join ';')
}

function Find-PythonExe {
    if (Test-CommandExists 'py') {
        try {
            $candidate = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                $candidate = (($candidate | Select-Object -First 1) -as [string]).Trim()
                if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                    return $candidate
                }
            }
        } catch {
        }
    }
    $local = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    if (Test-CommandExists 'python') { return (Get-Command 'python').Source }
    return $null
}

function Add-PythonScriptsToPath {
    $pythonExe = Find-PythonExe
    if (-not $pythonExe) { return }
    try {
        $scripts = (& $pythonExe -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null)
        if ($scripts) {
            $scripts = (($scripts | Select-Object -First 1) -as [string]).Trim()
        }
        if ($scripts -and (Test-Path -LiteralPath $scripts)) {
            if ($env:Path -notlike "*$scripts*") {
                $env:Path = "$scripts;$env:Path"
            }
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            if ($userPath -notlike "*$scripts*") {
                [Environment]::SetEnvironmentVariable('Path', "$scripts;$userPath", 'User')
                Write-Note "Added $scripts to your user PATH - reopen the browser to pick it up"
            }
        }
    } catch {
    }
}

function Add-OllamaToPath {
    $dirs = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama'),
        (Join-Path $env:ProgramFiles 'Ollama')
    )
    foreach ($dir in $dirs) {
        if ((Test-Path -LiteralPath (Join-Path $dir 'ollama.exe')) -and ($env:Path -notlike "*$dir*")) {
            $env:Path = "$dir;$env:Path"
            return
        }
    }
}

function Find-OllamaExe {
    if (Test-CommandExists 'ollama') {
        return (Get-Command 'ollama').Source
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Command
    )

    if ($Command -and (Test-CommandExists $Command)) {
        Write-Skip "$Label is already installed"
        return $true
    }

    if (-not (Test-CommandExists 'winget')) {
        Write-Bad "${Label}: winget is not available. Install it manually (package id: $Id)"
        return $false
    }

    if ($DryRun) {
        Write-Host "  would run: winget install --id $Id -e --source winget --silent --accept-package-agreements --accept-source-agreements"
        return $true
    }

    Write-Host "  winget install --id $Id -e"
    & winget install --id $Id -e --source winget --silent --accept-package-agreements --accept-source-agreements 2>&1 | ForEach-Object {
        Write-Host "    $_"
    }
    $code = $LASTEXITCODE
    Update-SessionPath

    if ($Command) {
        if (Test-CommandExists $Command) {
            Write-Ok "$Label installed"
            return $true
        }
        Write-Warn "${Label}: winget exited $code; '$Command' not found yet (reopen this window and check)"
        return $false
    }

    if ($code -ne 0) {
        Write-Warn "${Label}: winget exited with code $code"
        return $false
    }
    Write-Ok "$Label installed"
    return $true
}

function Install-NpmGlobal {
    param(
        [Parameter(Mandatory = $true)][string]$Package,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Command
    )

    if ($Command -and (Test-CommandExists $Command)) {
        Write-Skip "$Label is already installed"
        return $true
    }

    if (-not (Test-CommandExists 'npm')) {
        Write-Bad "${Label}: npm not found. Run -Groups core first, then reopen PowerShell."
        return $false
    }

    if ($DryRun) {
        Write-Host "  would run: npm install -g $Package"
        return $true
    }

    Write-Host "  npm install -g $Package"
    & npm install -g $Package 2>&1 | ForEach-Object {
        Write-Host "    $_"
    }
    $code = $LASTEXITCODE
    Update-SessionPath

    if ($Command -and (Test-CommandExists $Command)) {
        Write-Ok "$Label installed"
        return $true
    }
    if ($code -ne 0) {
        Write-Warn "${Label}: npm exited with code $code"
        return $false
    }
    Write-Note "${Label}: package installed; reopen the terminal if '$Command' is not found"
    return $true
}

function Install-PipPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Package,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Command
    )

    if ($Command -and (Test-CommandExists $Command)) {
        Write-Skip "$Label is already installed"
        return $true
    }

    $pythonExe = Find-PythonExe
    if (-not $pythonExe) {
        Write-Bad "${Label}: Python 3.12 not found. Run -Groups core first."
        return $false
    }

    if ($DryRun) {
        Write-Host "  would run: $pythonExe -m pip install --user --upgrade $Package"
        return $true
    }

    Write-Host "  $pythonExe -m pip install --user --upgrade $Package"
    & $pythonExe -m pip install --user --upgrade $Package 2>&1 | ForEach-Object {
        Write-Host "    $_"
    }
    $code = $LASTEXITCODE
    Add-PythonScriptsToPath

    if ($code -ne 0) {
        Write-Warn "${Label}: pip exited with code $code"
        return $false
    }
    if ($Command -and (Test-CommandExists $Command)) {
        Write-Ok "$Label installed"
    } else {
        Write-Note "${Label}: package installed; reopen the terminal if '$Command' is not found"
    }
    return $true
}

function Test-OllamaServer {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Ensure-Ollama {
    $model = $OllamaModel
    if ([string]::IsNullOrWhiteSpace($model)) {
        $model = 'llama3.2:3b'
    }

    Write-Head 'Ollama (local AI engine)'

    $ollamaExe = Find-OllamaExe

    if (-not $ollamaExe) {
        $setupScript = Join-Path $env:LOCALAPPDATA 'Programs\LuckyDBrowser\ollama_setup.ps1'
        if ($DryRun) {
            if (Test-Path -LiteralPath $setupScript) {
                Write-Host "  would run: powershell -NoProfile -ExecutionPolicy RemoteSigned -File `"$setupScript`" -Model $model"
            } else {
                Write-Host "  would run: winget install --id Ollama.Ollama -e; ollama pull $model"
            }
            return
        }

        if (Test-Path -LiteralPath $setupScript) {
            Write-Host '  Running the bundled Ollama bootstrap...'
            & powershell -NoProfile -ExecutionPolicy RemoteSigned -File $setupScript -Model $model
            $code = $LASTEXITCODE
            Update-SessionPath
            Add-OllamaToPath
            if ($code -ne 0) {
                Write-Warn "Bundled Ollama setup exited with code $code; trying the winget fallback"
            }
        }

        $ollamaExe = Find-OllamaExe
        if (-not $ollamaExe) {
            if (Install-WingetPackage -Id 'Ollama.Ollama' -Label 'Ollama' -Command 'ollama') {
                Update-SessionPath
                Add-OllamaToPath
            }
            $ollamaExe = Find-OllamaExe
        }
    } else {
        Write-Skip 'Ollama is already installed'
    }

    if (-not $ollamaExe) {
        Write-Bad "Ollama is not installed. Install it manually and re-run with -Groups ollama."
        return
    }

    if (-not (Test-OllamaServer)) {
        if ($DryRun) {
            Write-Host '  would start: ollama serve'
        } else {
            Write-Host '  Starting the Ollama server...'
            Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and -not (Test-OllamaServer)) {
                Start-Sleep -Seconds 2
            }
        }
    }

    if (Test-OllamaServer) {
        Write-Ok 'Ollama server is responding on 127.0.0.1:11434'
    } elseif (-not $DryRun) {
        Write-Bad "Ollama server did not start. Run 'ollama serve' manually and retry."
        return
    }

    if ($DryRun) {
        Write-Host "  would run: ollama pull $model"
        return
    }

    Add-OllamaToPath

    Write-Host "  Pulling model $model (this can take several minutes)..."
    & $ollamaExe pull $model 2>&1 | ForEach-Object {
        Write-Host "    $_"
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Model $model is available"
    } else {
        Write-Warn "Model pull exited with code $LASTEXITCODE; run 'ollama pull $model' manually"
    }
}

function Invoke-CoreGroup {
    Write-Head 'Core prerequisites'

    if (-not (Test-CommandExists 'winget')) {
        Write-Bad 'winget is not available. Install App Installer from the Microsoft Store, then re-run.'
        return
    }

    Install-WingetPackage -Id 'OpenJS.NodeJS.LTS' -Label 'Node.js LTS' -Command 'node' | Out-Null
    Install-WingetPackage -Id 'Git.Git' -Label 'Git' -Command 'git' | Out-Null
    Install-WingetPackage -Id 'GitHub.cli' -Label 'GitHub CLI' -Command 'gh' | Out-Null

    $pythonExe = Find-PythonExe
    $hasPython312 = $false
    if ($pythonExe) {
        try {
            $pyVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null)
            if ($pyVersion -eq '3.12') { $hasPython312 = $true }
        } catch {
        }
    }
    if ($hasPython312) {
        Write-Skip 'Python 3.12 is already installed'
    } else {
        Install-WingetPackage -Id 'Python.Python.3.12' -Label 'Python 3.12' -Command 'python' | Out-Null
    }

    Update-SessionPath
    Add-PythonScriptsToPath

    if ((Test-CommandExists 'node') -and -not (Test-CommandExists 'npm')) {
        Write-Warn 'Node.js is present but npm was not found. Reopen this window or reinstall Node.js LTS.'
    }

    Write-Note 'Close and reopen LuckyD Browser so its terminal inherits the new PATH.'
}

function Invoke-FreeGroup {
    Write-Head 'Free Agent Mesh CLIs'

    if (Test-CommandExists 'npm') {
        Install-NpmGlobal -Package '@qwen-code/qwen-code' -Label 'Qwen Code' -Command 'qwen' | Out-Null
        Install-NpmGlobal -Package 'openclaw' -Label 'OpenClaw' -Command 'openclaw' | Out-Null
        Install-NpmGlobal -Package 'cline' -Label 'Cline' -Command 'cline' | Out-Null
        Install-NpmGlobal -Package '@mariozechner/pi-coding-agent' -Label 'Pi' -Command 'pi' | Out-Null
        Install-NpmGlobal -Package '@google/jules' -Label 'Jules' -Command 'jules' | Out-Null
    } else {
        Write-Bad 'npm not found. Run -Groups core first, then reopen PowerShell.'
    }

    if (Test-CommandExists 'python') {
        Install-PipPackage -Package 'hermes-agent' -Label 'Hermes' -Command 'hermes' | Out-Null
    } else {
        Write-Bad 'python not found. Run -Groups core first, then reopen PowerShell.'
    }

    Install-WingetPackage -Id 'Google.AntigravityCLI' -Label 'Antigravity CLI' -Command 'agy' | Out-Null
    Write-Note 'Antigravity: run agy and complete Google sign-in on first use.'
}

function Invoke-KeyedGroup {
    Write-Head 'Keyed Agent Mesh CLIs (keys or seats required)'

    if (Test-CommandExists 'npm') {
        Install-NpmGlobal -Package '@anthropic-ai/claude-code' -Label 'Claude Code' -Command 'claude' | Out-Null
        Install-NpmGlobal -Package '@openai/codex' -Label 'Codex' -Command 'codex' | Out-Null
        Install-NpmGlobal -Package 'opencode-ai' -Label 'OpenCode' -Command 'opencode' | Out-Null
        Install-NpmGlobal -Package '@minimax-ai/code' -Label 'MiniMax Code' -Command 'mcode' | Out-Null
        Install-NpmGlobal -Package 'mmx-cli' -Label 'MiniMax Media' -Command 'mmx' | Out-Null
        Install-NpmGlobal -Package '@xai-official/grok' -Label 'Grok' -Command 'grok' | Out-Null
    } else {
        Write-Bad 'npm not found. Run -Groups core first, then reopen PowerShell.'
    }

    if (Test-CommandExists 'python') {
        Install-PipPackage -Package 'aider-chat' -Label 'Aider' -Command 'aider' | Out-Null
    } else {
        Write-Bad 'python not found. Run -Groups core first, then reopen PowerShell.'
    }

    Install-WingetPackage -Id 'GitHub.Copilot' -Label 'GitHub Copilot CLI' -Command 'copilot' | Out-Null
    Write-Note "Copilot: run 'gh auth login' for GitHub operations, then 'copilot login' if prompted."
    Write-Note 'Authentication commands are interactive. See section 5.3 of the setup guide.'
}

function Invoke-WslGroup {
    Write-Head 'WSL2 / Muse prerequisites'

    if (-not (Test-CommandExists 'wsl')) {
        Write-Bad 'wsl.exe not found. From an elevated PowerShell run: wsl --install -d Ubuntu'
        return
    }

    if ($DryRun) {
        Write-Host '  would check WSL distros and print Muse install instructions'
        return
    }

    $distros = @()
    try {
        $distros = @(& wsl -l -q 2>$null)
    } catch {
    }

    $hasUbuntu = $false
    foreach ($distro in $distros) {
        if ($distro -match 'Ubuntu') {
            $hasUbuntu = $true
            break
        }
    }

    if (-not $hasUbuntu) {
        Write-Warn 'No Ubuntu distro found. From an elevated PowerShell run: wsl --install -d Ubuntu (then reboot).'
        Write-Note 'After Ubuntu starts, run inside it: curl -fsSL https://dev.meta.ai/install.sh | bash'
        Write-Note 'Then authenticate with: muse login (or set META_API_KEY)'
        return
    }

    Write-Ok 'Ubuntu WSL distro found'
    Write-Note 'If Muse is missing, run inside Ubuntu: curl -fsSL https://dev.meta.ai/install.sh | bash'
    Write-Note 'Then authenticate with: muse login (or set META_API_KEY)'
}

$validGroups = @('core', 'ollama', 'free', 'keyed', 'wsl', 'all')
$requestedGroups = New-Object System.Collections.Generic.List[string]
foreach ($groupValue in $Groups) {
    if ($null -eq $groupValue) { continue }
    foreach ($part in $groupValue.Split(',')) {
        $name = $part.Trim().ToLowerInvariant()
        if (-not $name) { continue }
        if ($validGroups -notcontains $name) {
            Write-Host "Unknown group '$name'. Valid groups: $($validGroups -join ', ')" -ForegroundColor Red
            exit 2
        }
        if (-not $requestedGroups.Contains($name)) {
            $requestedGroups.Add($name)
        }
    }
}

if ($requestedGroups.Count -eq 0) {
    Write-Host "No groups specified. Valid groups: $($validGroups -join ', ')" -ForegroundColor Red
    exit 2
}

$selectedGroups = New-Object System.Collections.Generic.List[string]
foreach ($group in $requestedGroups) {
    if ($group -eq 'all') {
        foreach ($allGroup in @('core', 'ollama', 'free', 'keyed', 'wsl')) {
            if (-not $selectedGroups.Contains($allGroup)) { $selectedGroups.Add($allGroup) }
        }
    } elseif (-not $selectedGroups.Contains($group)) {
        $selectedGroups.Add($group)
    }
}

Write-Host 'LuckyD Browser - agent setup bootstrap' -ForegroundColor Cyan
Write-Host "Groups: $($selectedGroups -join ', ')"
if ($DryRun) {
    Write-Host 'Mode: DRY RUN (no changes will be made)' -ForegroundColor Yellow
}

Update-SessionPath
Add-PythonScriptsToPath

foreach ($group in $selectedGroups) {
    switch ($group) {
        'core'   { Invoke-CoreGroup }
        'ollama' { Ensure-Ollama }
        'free'   { Invoke-FreeGroup }
        'keyed'  { Invoke-KeyedGroup }
        'wsl'    { Invoke-WslGroup }
    }
}

Write-Head 'Summary'

if ($script:Failures.Count -eq 0 -and $script:Warnings.Count -eq 0) {
    Write-Host 'All requested steps completed without errors.' -ForegroundColor Green
} else {
    if ($script:Warnings.Count -gt 0) {
        Write-Host 'Warnings:' -ForegroundColor Yellow
        foreach ($warning in $script:Warnings) {
            Write-Host " - $warning" -ForegroundColor Yellow
        }
    }
    if ($script:Failures.Count -gt 0) {
        Write-Host 'Failures:' -ForegroundColor Red
        foreach ($failure in $script:Failures) {
            Write-Host " - $failure" -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host ' 1. Close and reopen LuckyD Browser so its terminal inherits PATH.'
Write-Host ' 2. Authenticate the mesh CLIs you installed (guide section 5.3).'
Write-Host ' 3. In the browser terminal run /providers and /doctor.'
Write-Host ' 4. Read section 9 of LuckyD-Browser-Setup-Guide.md before approving tool calls.'
Write-Host ''
Write-Host 'If a newly installed command is still missing, reopen PowerShell and run:'
Write-Host '  where.exe node npm git gh python ollama'
