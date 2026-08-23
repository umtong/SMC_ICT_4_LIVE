param(
    [Parameter(Mandatory=$true)][datetime]$Start,
    [Parameter(Mandatory=$true)][datetime]$DevelopmentEnd,
    [Parameter(Mandatory=$true)][datetime]$End,
    [int]$WarmupDays = 75,
    [string]$Name = "continuous"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
$output = Join-Path $CandidateRoot ("runtime\historical\" + $Name)
$cache = Join-Path $CandidateRoot "runtime\market-cache"
New-Item -ItemType Directory -Force $output,$cache | Out-Null
Invoke-ProductionPython -m production.cli historical-continuous `
    --start $Start.ToString("yyyy-MM-dd") `
    --development-end $DevelopmentEnd.ToString("yyyy-MM-dd") `
    --end $End.ToString("yyyy-MM-dd") `
    --warmup-days "$WarmupDays" `
    --cache $cache `
    --output $output
Write-Host "Continuous account evidence: $output"
