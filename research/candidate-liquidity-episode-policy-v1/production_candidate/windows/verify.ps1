[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run bootstrap.ps1 first." }
& $Python -m compileall -q src tests
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed with exit code $LASTEXITCODE." }
& $Python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE." }
& $Python -m smc_ict_4.episode_policy_live.cli verify `
    --build-node `
    --state artifacts\episode-policy-live\verify.sqlite `
    --output artifacts\episode-policy-live\verify.json
if ($LASTEXITCODE -ne 0) { throw "lep verify failed with exit code $LASTEXITCODE." }
Write-Host "Windows reproduction verification completed."
