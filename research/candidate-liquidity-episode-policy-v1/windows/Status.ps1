param(
    [ValidateSet("shadow","paper","testnet")][string]$Mode = "shadow"
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Venv
$configName = if ($Mode -eq "testnet") { "testnet.windows.example.json" } else { "$Mode.windows.json" }
$config = Join-Path $CandidateRoot ("configs\" + $configName)
Invoke-ProductionPython -m production.cli status --config $config
