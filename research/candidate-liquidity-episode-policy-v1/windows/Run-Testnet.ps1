param(
    [Parameter(Mandatory=$true)][switch]$IUnderstandThisSubmitsTestnetOrders,
    [double]$DurationSeconds = 0
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
if (-not $IUnderstandThisSubmitsTestnetOrders) { throw "Explicit testnet-order acknowledgement is required." }
if (-not $env:BINANCE_API_KEY -or -not $env:BINANCE_API_SECRET) {
    throw "BINANCE_API_KEY and BINANCE_API_SECRET must contain Binance USD-M Futures testnet credentials."
}
$config = Join-Path $CandidateRoot "configs\testnet.windows.example.json"
$model = Join-Path $CandidateRoot "runtime\model\model_bundle.joblib"
if (-not (Test-Path $model)) { throw "Causal model bundle is missing. Run windows\Build-Model.ps1 first." }
$args = @("-m", "production.cli", "testnet", "--config", $config)
if ($DurationSeconds -gt 0) { $args += @("--duration-seconds", "$DurationSeconds") }
Invoke-ProductionPython @args
