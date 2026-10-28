# Verify the local checkout follows TradingPlatformNext's main-only workflow.
# Run from any directory inside the repository:
#   powershell -ExecutionPolicy Bypass -File .\scripts\verify-main-workflow.ps1

$ErrorActionPreference = "Stop"

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    Write-Error "Not inside a Git repository. Stop; do not create a branch or PR as a workaround."
    exit 1
}

$repoRoot = (Resolve-Path $repoRoot).Path
$origin = git -C $repoRoot remote get-url origin 2>$null
$branch = git -C $repoRoot branch --show-current 2>$null

if ($origin -notmatch 'github\.com[:/]sunilraykar/TradingPlatformNext(?:\.git)?$') {
    Write-Error "Unexpected origin remote '$origin'. Expected sunilraykar/TradingPlatformNext. Stop before editing."
    exit 1
}

if ($branch -ne "main") {
    Write-Error "Current branch is '$branch', not 'main'. Do not create or use another branch. Stop and switch to the existing main branch only after preserving any work."
    exit 1
}

Write-Host "PASS: repository origin is TradingPlatformNext and current branch is main." -ForegroundColor Green

$status = git -C $repoRoot status --short
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not inspect Git status."
    exit 1
}

if ($status) {
    Write-Host ""
    Write-Host "WARNING: Working tree has changes. Review and preserve them before editing:" -ForegroundColor Yellow
    Write-Host $status
} else {
    Write-Host "Working tree is clean."
}

Write-Host ""
Write-Host "This is a preflight check, not a Git hook; run it before starting work."
