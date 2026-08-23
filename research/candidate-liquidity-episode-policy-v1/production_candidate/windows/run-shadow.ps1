[CmdletBinding()]
param([int]$DurationSeconds = 0)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
$Args = @("-m", "smc_ict_4.episode_policy_live.cli", "run", "--mode", "shadow", "--state", "artifacts\episode-policy-live\shadow.sqlite")
if ($DurationSeconds -gt 0) { $Args += @("--duration-seconds", "$DurationSeconds") }
& .\.venv\Scripts\python.exe @Args
if ($LASTEXITCODE -ne 0) { throw "lep shadow run failed with exit code $LASTEXITCODE." }
