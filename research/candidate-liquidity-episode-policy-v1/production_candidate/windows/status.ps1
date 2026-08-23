[CmdletBinding()]
param([ValidateSet("shadow", "sandbox", "testnet")][string]$Mode = "shadow")
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
& .\.venv\Scripts\python.exe -m smc_ict_4.episode_policy_live.cli status `
    --state "artifacts\episode-policy-live\$Mode.sqlite" `
    --mode $Mode
if ($LASTEXITCODE -ne 0) { throw "lep status failed with exit code $LASTEXITCODE." }
