[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string[]]$MonthlyRoot,
    [string]$MetricsRoot = "",
    [string]$OutputName = "continuous-2024-01-01-2026-08-01",
    [ValidateRange(0, 365)][int]$WarmupDays = 90,
    [double]$InitialNav = 100000.0
)
$ErrorActionPreference = "Stop"

Write-Host "This command reads existing official Binance Vision futures_um/monthly archives."
Write-Host "It does not download or synthesize missing trade, funding-rate, or mark-price months."
& (Join-Path $PSScriptRoot "run-replay.ps1") `
    -Start "2024-01-01" `
    -End "2026-08-01" `
    -MonthlyRoot $MonthlyRoot `
    -MetricsRoot $MetricsRoot `
    -OutputName $OutputName `
    -WarmupDays $WarmupDays `
    -InitialNav $InitialNav
if ($LASTEXITCODE -ne 0) { throw "run-replay.ps1 failed with exit code $LASTEXITCODE." }
