Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:CandidateRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:RepoRoot = (Resolve-Path (Join-Path $script:CandidateRoot "..\..")).Path
$script:VenvRoot = Join-Path $script:CandidateRoot ".venv"
$script:Python = Join-Path $script:VenvRoot "Scripts\python.exe"

function Assert-Windows11 {
    if ($env:OS -ne "Windows_NT") { throw "This script is the native Windows path and requires Windows." }
    $os = Get-CimInstance Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    if ($build -lt 22000) { throw "Windows 11 build 22000 or later is required; detected $build." }
}

function Assert-Venv {
    if (-not (Test-Path $script:Python)) {
        throw "Python environment not found at $script:Python. Run windows\Bootstrap.ps1 first."
    }
}

function Invoke-ProductionPython {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    Assert-Venv
    Push-Location $script:CandidateRoot
    try {
        & $script:Python @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }
}
