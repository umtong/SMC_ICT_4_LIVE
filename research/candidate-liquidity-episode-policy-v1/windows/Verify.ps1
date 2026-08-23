param(
    [string]$Config = "configs\shadow.windows.json"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11
Assert-Venv
$output = Join-Path $CandidateRoot "runtime\verification"
New-Item -ItemType Directory -Force $output | Out-Null
$configPath = (Resolve-Path (Join-Path $CandidateRoot $Config)).Path
Invoke-ProductionPython -m production.cli verify --config $configPath --output (Join-Path $output "contract.json")
Invoke-ProductionPython -m production.cli nautilus-smoke --output (Join-Path $output "nautilus-smoke")
Invoke-ProductionPython -m production.cli reconcile --database (Join-Path $output "nautilus-smoke\runtime.sqlite3") --output (Join-Path $output "reconciliation.json")
Write-Host "Windows/Nautilus verification completed: $output"
