$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "backend")
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) { throw "Virtual environment missing. Run scripts\setup-local.ps1 first." }
. .\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
