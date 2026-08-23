param(
    [datetime]$Start = [datetime]"2024-01-01",
    [datetime]$DevelopmentEnd = [datetime]"2024-10-01",
    [datetime]$End = [datetime]"2025-01-01",
    [int]$WarmupDays = 75
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
$root = Join-Path $CandidateRoot "runtime\model-research"
$cache = Join-Path $CandidateRoot "runtime\market-cache"
$modelDir = Join-Path $CandidateRoot "runtime\model"
New-Item -ItemType Directory -Force $root,$cache,$modelDir | Out-Null
Invoke-ProductionPython -m production.cli historical-continuous `
    --start $Start.ToString("yyyy-MM-dd") `
    --development-end $DevelopmentEnd.ToString("yyyy-MM-dd") `
    --end $End.ToString("yyyy-MM-dd") `
    --warmup-days "$WarmupDays" `
    --cache $cache `
    --output $root
Invoke-ProductionPython -m production.cli build-model `
    --root (Join-Path $root "harvest") `
    --output (Join-Path $modelDir "model_bundle.joblib") `
    --cutoff ($DevelopmentEnd.ToUniversalTime().ToString("o")) `
    --risk-fraction 0.03
Write-Host "Model bundle built at $modelDir"
