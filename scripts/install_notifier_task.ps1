[CmdletBinding()]
param([string]$TaskName = "Notifierr")

$ErrorActionPreference = "Stop"
$Runner = Join-Path $PSScriptRoot "run_notifier.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -Hidden -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$registered = Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force -ErrorAction Stop
if (-not $registered -or -not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Scheduled task '$TaskName' was not registered. Run this installer from an elevated PowerShell window."
}
Write-Host "Installed scheduled task '$TaskName'."
