[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $Root
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install official CPython 3.13 x64."
}
$VersionOutput = & py -3.13 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 discovery failed with exit code $LASTEXITCODE." }
$Version = $VersionOutput.Trim()
if ($Version -ne "3.13") { throw "CPython 3.13 is required, found $Version" }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed with exit code $LASTEXITCODE." }
}
$Python = ".\.venv\Scripts\python.exe"
& $Python -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw "ensurepip failed with exit code $LASTEXITCODE." }
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE." }
& $Python -m pip install --editable . "pytest==9.1.1"
if ($LASTEXITCODE -ne 0) { throw "Production candidate installation failed with exit code $LASTEXITCODE." }
New-Item -ItemType Directory -Force artifacts\episode-policy-live | Out-Null
New-Item -ItemType Directory -Force artifacts\episode-policy-replay | Out-Null
Write-Host "Installed the production candidate and its contract-test runner into $Root\.venv"
