[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCandidates = @()
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonCandidates += $pythonCommand.Source
    }

    $userPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $userPythonRoot) {
        $pythonCandidates += Get-ChildItem -LiteralPath $userPythonRoot -Directory -Filter "Python*" |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "python.exe" } |
            Where-Object { Test-Path -LiteralPath $_ }
    }

    $hostPython = $null
    $version = $null
    foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
        $candidateVersion = & $candidate -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
        if ([version]$candidateVersion -ge [version]"3.11") {
            $hostPython = $candidate
            $version = $candidateVersion
            break
        }
    }
    if (-not $hostPython) {
        throw "Python 3.11+ was not found. Install it from python.org, then run this script again."
    }

    & $hostPython -m venv $venvPath
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install `
    pydantic pydantic-settings keyring pyyaml jsonschema `
    fastapi "uvicorn[standard]" discord.py apscheduler sqlalchemy duckdb pyarrow httpx websockets `
    pytest ruff

Write-Host ""
Write-Host "Cockpit environment ready."
Write-Host "Run tests: .\\.venv\\Scripts\\python.exe -m pytest -q app\\tests"
Write-Host "Run lint:  .\\.venv\\Scripts\\ruff.exe check app"
Write-Host "Authenticate Codex in this Windows account: codex login --device-auth"
