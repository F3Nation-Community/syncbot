#requires -Version 5.1
<#
.SYNOPSIS
  SyncBot root deploy launcher for Windows (PowerShell).

  Verifies a bash environment (Git Bash or WSL), scans infra/*/scripts/deploy.sh,
  then runs the selected script in bash — same contract as ./deploy.sh on macOS/Linux.

  Provider-specific prerequisite checks live in infra/<provider>/scripts/deploy.sh
  (sourcing repo-root deploy.sh for shared helpers). There are no deploy.ps1 files under infra/.

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 aws
  .\deploy.ps1 1
#>
param(
    [Parameter(Position = 0)]
    [string] $Selection = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ScriptArgs
)

$ErrorActionPreference = "Stop"

function Find-GitBash {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "${env:ProgramFiles}\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "${env:LocalAppData}\Programs\Git\bin\bash.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Test-WslBashWorks {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $null = & wsl.exe -e bash -c "echo wsl_ok" 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Convert-WindowsPathToWsl {
    param([string] $WindowsPath)
    $full = (Resolve-Path -LiteralPath $WindowsPath).Path
    if ($full -match '^([A-Za-z]):[\\/](.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $tail = $Matches[2] -replace '\\', '/'
        return "/mnt/$drive/$tail"
    }
    throw "Cannot map path to WSL (expected C:\...): $WindowsPath"
}

function Find-DeployBash {
    $gitBash = Find-GitBash
    if ($gitBash) {
        return [pscustomobject]@{ Kind = 'GitBash'; Executable = $gitBash }
    }
    if (Test-WslBashWorks) {
        return [pscustomobject]@{ Kind = 'Wsl'; Executable = 'wsl.exe' }
    }
    $bashCmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($bashCmd) {
        return [pscustomobject]@{ Kind = 'Path'; Executable = $bashCmd.Source }
    }
    return $null
}

function Show-WindowsPrereqStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepoRoot,
        [Parameter(Mandatory = $true)]
        $BashInfo
    )
    Write-Host ""
    Write-Host "=== SyncBot Deploy (Windows) ==="
    Write-Host "Repository: $RepoRoot"
    Write-Host ""
    Write-Host "Bash environment:"
    switch ($BashInfo.Kind) {
        'GitBash' {
            Write-Host "  Git Bash: $($BashInfo.Executable)" -ForegroundColor Green
            if (Test-WslBashWorks) {
                Write-Host "  WSL:      available (not used; Git Bash preferred)" -ForegroundColor DarkGray
            } else {
                Write-Host "  WSL:      not found or not ready" -ForegroundColor DarkGray
            }
        }
        'Wsl' {
            Write-Host "  Git Bash: not found" -ForegroundColor DarkGray
            Write-Host "  WSL:      bash (will run deploy.sh with Windows paths mapped to /mnt/...)" -ForegroundColor Green
        }
        'Path' {
            Write-Host "  bash:     $($BashInfo.Executable)" -ForegroundColor Green
        }
    }
    Write-Host ""
}

function Invoke-DeploySh {
    param(
        [Parameter(Mandatory = $true)]
        $BashInfo,
        [Parameter(Mandatory = $true)]
        [string] $ScriptPath,
        [string[]] $BashArgs
    )
    $extra = if ($null -ne $BashArgs -and $BashArgs.Count -gt 0) { @($BashArgs) } else { @() }
    if ($BashInfo.Kind -eq 'Wsl') {
        $wslPath = Convert-WindowsPathToWsl -WindowsPath $ScriptPath
        & wsl.exe -e bash $wslPath @extra
    } else {
        & $BashInfo.Executable $ScriptPath @extra
    }
}

function Show-Usage {
    @"
Usage: .\deploy.ps1 [selection] [provider-script-args...]

No args:
  Scan infra/*/scripts/deploy.sh, show a numbered menu, and run your choice.

With [selection]:
  - provider name (e.g. aws, gcp), OR
  - menu index (e.g. 1, 2)
"@
}

function Get-DeployScripts {
    param([string] $RepoRoot)
    $infraDir = Join-Path $RepoRoot "infra"
    if (-not (Test-Path -LiteralPath $infraDir)) { return @() }

    $providers = Get-ChildItem -LiteralPath $infraDir -Directory -ErrorAction SilentlyContinue | Sort-Object Name
    $results = @()
    foreach ($provider in $providers) {
        $scriptPath = Join-Path $provider.FullName "scripts/deploy.sh"
        if (Test-Path -LiteralPath $scriptPath) {
            $results += [pscustomobject]@{
                Provider = $provider.Name
                Path = $scriptPath
            }
        }
    }
    return $results
}

function Resolve-Selection {
    param(
        [array] $Entries,
        [string] $Selection
    )

    if ($Selection -match '^\d+$') {
        $index = [int]$Selection
        if ($index -ge 1 -and $index -le $Entries.Count) {
            return $Entries[$index - 1]
        }
        return $null
    }

    foreach ($entry in $Entries) {
        if ($entry.Provider -ieq $Selection) {
            return $entry
        }
    }
    return $null
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Selection -in @("-h", "--help", "help")) {
    Show-Usage
    exit 0
}

$bashInfo = Find-DeployBash
if (-not $bashInfo) {
    Write-Host @"
Error: no bash found. Install one of:

  • Git for Windows (Git Bash): https://git-scm.com/download/win
  • WSL (Windows Subsystem for Linux): https://learn.microsoft.com/windows/wsl/install

Then re-run: .\deploy.ps1
"@ -ForegroundColor Red
    exit 1
}

Show-WindowsPrereqStatus -RepoRoot $RepoRoot -BashInfo $bashInfo

$entries = Get-DeployScripts -RepoRoot $RepoRoot
if ($entries.Count -eq 0) {
    Write-Error "No deploy scripts found under infra/*/scripts/deploy.sh"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Selection)) {
    Write-Host "Discovered deploy scripts:"
    for ($i = 0; $i -lt $entries.Count; $i++) {
        $n = $i + 1
        $relativePath = $entries[$i].Path
        if ($relativePath.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            $relativePath = $relativePath.Substring($RepoRoot.Length).TrimStart('\', '/')
        }
        Write-Host "  $n) $($entries[$i].Provider) ($relativePath)"
    }
    Write-Host "  0) Exit"
    Write-Host ""
    $choice = Read-Host "Choose provider [1]"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }
    if ($choice -eq "0") { exit 0 }
    $Selection = $choice
}

$selected = Resolve-Selection -Entries $entries -Selection $Selection
if (-not $selected) {
    Write-Host "Invalid selection: $Selection" -ForegroundColor Red
    Write-Host ""
    Show-Usage
    exit 1
}

Write-Host "Running: $($selected.Path)"
Invoke-DeploySh -BashInfo $bashInfo -ScriptPath $selected.Path -BashArgs $ScriptArgs
exit $LASTEXITCODE
