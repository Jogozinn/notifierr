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

function Test-NotifierrProcess {
    param([int]$ProcessId)
    try {
        $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $CommandLine = [string]$ProcessInfo.CommandLine
        return ($CommandLine -match "uvicorn" -and $CommandLine -match "backend\.main:app")
    } catch {
        return $false
    }
}

$Pids = New-Object System.Collections.Generic.List[int]
if (Test-Path -LiteralPath $PidPath) {
    try {
        $PidRecord = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
        if ($PidRecord.pid) {
            $Pids.Add([int]$PidRecord.pid)
        }
    } catch {
        Write-Warning "Could not read ${PidPath}: $($_.Exception.Message)"
    }
}

$ListeningPid = Get-ListeningPid
if ($ListeningPid -and -not $Pids.Contains([int]$ListeningPid)) {
    $Health = Get-NotifierrHealth
    if (Test-NotifierrProcess -ProcessId ([int]$ListeningPid)) {
        $Pids.Add([int]$ListeningPid)
    } elseif ($Health -and $Health.ok -eq $true) {
        $Pids.Add([int]$ListeningPid)
    } else {
        Write-Warning "Port ${HostAddress}:$Port is owned by pid=$ListeningPid, but it was not verified as Notifierr; leaving it running."
    }
}

if ($Pids.Count -eq 0) {
    Write-Host "No verified Notifierr backend process found for ${HostAddress}:$Port."
    exit 0
}

foreach ($PidValue in ($Pids | Select-Object -Unique)) {
    $Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
    if (-not $Process) {
        continue
    }
    Write-Host "Stopping Notifierr pid=$PidValue."
    Stop-Process -Id $PidValue -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
    if ($Process) {
        Stop-Process -Id $PidValue -Force -ErrorAction SilentlyContinue
    }
}

Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
Write-Host "Notifierr stop request complete."
