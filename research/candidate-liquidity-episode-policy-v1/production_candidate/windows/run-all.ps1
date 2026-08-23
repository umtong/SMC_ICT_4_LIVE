[CmdletBinding()]
param(
    [ValidateSet("shadow", "sandbox")][string]$ConnectedMode = "shadow",
    [ValidateRange(15, 86400)][int]$ConnectedSeconds = 60,
    [switch]$BuildDocker
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root

& (Join-Path $PSScriptRoot "bootstrap.ps1")
if ($LASTEXITCODE -ne 0) { throw "bootstrap.ps1 failed with exit code $LASTEXITCODE." }
& (Join-Path $PSScriptRoot "verify.ps1")
if ($LASTEXITCODE -ne 0) { throw "verify.ps1 failed with exit code $LASTEXITCODE." }

if ($BuildDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was requested but docker.exe was not found. Start Docker Desktop first."
    }
    & docker build `
        --file research/candidate-liquidity-episode-policy-v1/production_candidate/Dockerfile `
        --tag smc-ict-liquidity-episode:local `
        .
    if ($LASTEXITCODE -ne 0) { throw "Docker image build failed with exit code $LASTEXITCODE." }
}

$RuntimeScript = if ($ConnectedMode -eq "sandbox") { "run-paper.ps1" } else { "run-shadow.ps1" }
& (Join-Path $PSScriptRoot $RuntimeScript) -DurationSeconds $ConnectedSeconds
if ($LASTEXITCODE -ne 0) { throw "$RuntimeScript failed with exit code $LASTEXITCODE." }
& (Join-Path $PSScriptRoot "status.ps1") -Mode $ConnectedMode
if ($LASTEXITCODE -ne 0) { throw "status.ps1 failed with exit code $LASTEXITCODE." }

Write-Host "Contract tests, Nautilus node build, and the bounded $ConnectedMode connection completed."
Write-Host "This confirms runtime connectivity and durable-state integrity; it is not a performance result."
