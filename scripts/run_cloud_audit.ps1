$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Notifierr virtual environment was not found at $python"
}
if (-not $env:AUDIT_DATABASE_URL) {
    throw "AUDIT_DATABASE_URL is required. Use the dedicated read-only PostgreSQL role."
}

Push-Location $repoRoot
try {
    & $python -m backend.scripts.cloud_audit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
