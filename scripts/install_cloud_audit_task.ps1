param(
    [string]$TaskName = "Notifierr Cloud Evidence Audit",
    [string]$At = "10:00"
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "run_cloud_audit.ps1"
if (-not (Test-Path -LiteralPath $runner)) { throw "Audit runner not found at $runner" }
if (-not [Environment]::GetEnvironmentVariable("AUDIT_DATABASE_URL", "User")) {
    throw "Set AUDIT_DATABASE_URL as a user environment variable before installing the task."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Read-only daily Notifierr cloud evidence audit" -Force | Out-Null
Write-Host "Installed scheduled task '$TaskName' for $At (and next PC availability)."
