param(
    [switch]$ForceRecreate
)
. (Join-Path $PSScriptRoot "Common.ps1")
Assert-Windows11

if ($ForceRecreate -and (Test-Path $VenvRoot)) { Remove-Item -Recurse -Force $VenvRoot }
if (-not (Test-Path $Python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3.13 -m venv $VenvRoot
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction Stop
        & $pythonCommand.Source -m venv $VenvRoot
    }
}
Assert-Venv
& $Python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }
& $Python -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "repository installation failed" }
& $Python -m pip install --requirement (Join-Path $CandidateRoot "production-requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "production dependencies failed" }

$evidence = Join-Path $CandidateRoot "runtime\bootstrap"
New-Item -ItemType Directory -Force $evidence | Out-Null
Invoke-ProductionPython -m production.cli verify --config (Join-Path $CandidateRoot "configs\shadow.windows.json") --output (Join-Path $evidence "verify.json")
& $Python -c "import json,platform,nautilus_trader,sklearn; print(json.dumps({'python':platform.python_version(),'platform':platform.platform(),'nautilus':nautilus_trader.__version__,'sklearn':sklearn.__version__},indent=2))" | Set-Content -Encoding utf8 (Join-Path $evidence "versions.json")
Write-Host "Bootstrap completed. Evidence: $evidence"
