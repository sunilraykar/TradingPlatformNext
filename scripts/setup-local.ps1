$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "backend")
if (-not (Test-Path ".venv")) { py -3.11 -m venv .venv }
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Write-Host "Dependencies installed. Copy backend\.env.example to backend\.env and configure MySQL/Kite credentials."
