[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDirectory = Join-Path $ProjectRoot "logs"
$PidPath = Join-Path $LogDirectory "notifierr.pid.json"

function Get-NotifierrHealth {
    try {
        return Invoke-RestMethod -Uri "http://${HostAddress}:$Port/health" -Method Get -TimeoutSec 2 -ErrorAction Stop
    } catch {
        return $null
    }
}

function Get-ListeningPid {
    try {
        $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalAddress -eq $HostAddress -or $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::" }
        $Connection = $Connections | Select-Object -First 1
        if ($Connection) {
            return [int]$Connection.OwningProcess
        }
    } catch {
        $Lines = & netstat.exe -ano -p tcp 2>$null | Select-String -Pattern (":{0}\s" -f $Port)
        foreach ($Line in $Lines) {
            $Parts = ($Line.ToString().Trim() -split "\s+")
            if ($Parts.Count -ge 5 -and $Parts[3] -eq "LISTENING") {
                return [int]$Parts[4]
            }
        }
    }
    return $null
}

$PidRecord = $null
if (Test-Path -LiteralPath $PidPath) {
    try {
        $PidRecord = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
    } catch {
        $PidRecord = @{ unreadable = $true; path = $PidPath }
    }
}

$Task = $null
try {
    $Task = Get-ScheduledTask -TaskName "Notifierr" -ErrorAction Stop
} catch {
    $Task = $null
}

[ordered]@{
    host = $HostAddress
    port = $Port
    listening_pid = Get-ListeningPid
    health = Get-NotifierrHealth
    pid_record = $PidRecord
    scheduled_task = if ($Task) {
        [ordered]@{
            task_name = $Task.TaskName
            state = $Task.State.ToString()
            path = $Task.TaskPath
        }
    } else {
        $null
    }
} | ConvertTo-Json -Depth 8
