[CmdletBinding()]
param(
    [ValidateSet("shadow", "sandbox", "testnet")][string]$Mode = "shadow",
    [string]$Output = ""
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
if (-not $Output) {
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Output = "artifacts\episode-policy-live\backup-$Mode-$Stamp.sqlite"
}
& .\.venv\Scripts\python.exe -m smc_ict_4.episode_policy_live.cli backup `
    --state "artifacts\episode-policy-live\$Mode.sqlite" --output $Output
if ($LASTEXITCODE -ne 0) { throw "lep backup failed with exit code $LASTEXITCODE." }
