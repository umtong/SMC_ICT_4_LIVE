[CmdletBinding()]
param(
    [switch]$ConfirmTestnet,
    [int]$DurationSeconds = 0
)
$ErrorActionPreference = "Stop"
if (-not $ConfirmTestnet) { throw "Pass -ConfirmTestnet to enable Binance Futures testnet execution." }
if (-not $env:BINANCE_API_KEY -or -not $env:BINANCE_API_SECRET) {
    throw "BINANCE_API_KEY and BINANCE_API_SECRET must contain testnet credentials."
}
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
$Args = @("-m", "smc_ict_4.episode_policy_live.cli", "run", "--mode", "testnet", "--confirm-testnet", "--state", "artifacts\episode-policy-live\testnet.sqlite")
if ($DurationSeconds -gt 0) { $Args += @("--duration-seconds", "$DurationSeconds") }
& .\.venv\Scripts\python.exe @Args
if ($LASTEXITCODE -ne 0) { throw "lep testnet run failed with exit code $LASTEXITCODE." }
