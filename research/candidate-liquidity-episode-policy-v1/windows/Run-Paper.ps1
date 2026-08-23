param(
    [double]$DurationSeconds = 0
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
$config = Join-Path $CandidateRoot "configs\paper.windows.json"
$model = Join-Path $CandidateRoot "runtime\model\model_bundle.joblib"
if (-not (Test-Path $model)) { throw "Causal model bundle is missing. Run windows\Build-Model.ps1 first." }
Invoke-ProductionPython -m production.cli doctor --config $config --output (Join-Path $CandidateRoot "runtime\episode-policy-paper\evidence\doctor.json")
$args = @("-m", "production.cli", "paper", "--config", $config)
if ($DurationSeconds -gt 0) { $args += @("--duration-seconds", "$DurationSeconds") }
Invoke-ProductionPython @args
