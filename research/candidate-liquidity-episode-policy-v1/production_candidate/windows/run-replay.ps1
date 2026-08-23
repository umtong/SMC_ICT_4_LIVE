[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Start,
    [Parameter(Mandatory=$true)][string]$End,
    [Parameter(Mandatory=$true)][string[]]$MonthlyRoot,
    [string]$MetricsRoot = "",
    [string]$OutputName = "continuous",
    [int]$WarmupDays = 90,
    [double]$InitialNav = 100000.0
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run bootstrap.ps1 first." }
$ReplayArgs = @(
    "-m", "smc_ict_4.episode_policy_live.cli", "replay",
    "--start", $Start,
    "--end", $End,
    "--output", "artifacts\episode-policy-replay\$OutputName",
    "--warmup-days", "$WarmupDays",
    "--initial-nav", "$InitialNav"
)
foreach ($SourceRoot in $MonthlyRoot) {
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "Binance Vision monthly root does not exist: $SourceRoot"
    }
    $ReplayArgs += @("--monthly-root", (Resolve-Path -LiteralPath $SourceRoot).Path)
}
if ($MetricsRoot) {
    if (-not (Test-Path -LiteralPath $MetricsRoot -PathType Container)) {
        throw "Binance Vision daily metrics root does not exist: $MetricsRoot"
    }
    $ReplayArgs += @("--metrics-root", (Resolve-Path -LiteralPath $MetricsRoot).Path)
}
& $Python @ReplayArgs
if ($LASTEXITCODE -ne 0) { throw "lep replay failed with exit code $LASTEXITCODE." }
