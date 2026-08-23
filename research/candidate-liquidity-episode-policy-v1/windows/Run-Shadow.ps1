param(
    [double]$DurationSeconds = 0,
    [switch]$Once
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
if ($env:BINANCE_API_KEY -or $env:BINANCE_API_SECRET) {
    throw "Shadow mode is physically no-order. Remove BINANCE_API_KEY and BINANCE_API_SECRET from this process."
}
$config = Join-Path $CandidateRoot "configs\shadow.windows.json"
Invoke-ProductionPython -m production.cli doctor --config $config --output (Join-Path $CandidateRoot "runtime\episode-policy-shadow\evidence\doctor.json")
$args = @("-m", "production.cli", "shadow", "--config", $config)
if ($Once) { $args += "--once" }
elseif ($DurationSeconds -gt 0) { $args += @("--duration-seconds", "$DurationSeconds") }
Invoke-ProductionPython @args
