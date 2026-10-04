[CmdletBinding()]
param(
    [int]$Hours = 24,
    [int]$IntervalSeconds = 300,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
if (-not $OutputPath) {
    $OutputPath = Join-Path $LogDirectory ("soak-{0}.jsonl" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$Deadline = (Get-Date).ToUniversalTime().AddHours($Hours)
Set-Location -LiteralPath $ProjectRoot

do {
    $sample = & $Python -m backend.scripts.report_soak_sample
    Add-Content -LiteralPath $OutputPath -Value $sample -Encoding UTF8
    if ((Get-Date).ToUniversalTime() -lt $Deadline) {
        Start-Sleep -Seconds $IntervalSeconds
    }
} while ((Get-Date).ToUniversalTime() -lt $Deadline)

Write-Output $OutputPath
