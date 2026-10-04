[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [int]$HealthTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found at $Python"
}

$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$DateStamp = Get-Date -Format "yyyyMMdd"
$LauncherLogPath = Join-Path $LogDirectory ("notifierr-launcher-{0}.log" -f $DateStamp)
$StdoutLogPath = Join-Path $LogDirectory ("notifierr-{0}.out.log" -f $DateStamp)
$StderrLogPath = Join-Path $LogDirectory ("notifierr-{0}.err.log" -f $DateStamp)
$StdinPath = Join-Path $LogDirectory "notifierr.stdin"
$PidPath = Join-Path $LogDirectory "notifierr.pid.json"

function Write-LauncherLog {
    param([string]$Message)
    $Line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -LiteralPath $LauncherLogPath -Value $Line
}

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

function Clear-OutboundProxyEnvironment {
    $ProxyNames = @(
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"
    )
    foreach ($Name in $ProxyNames) {
        [Environment]::SetEnvironmentVariable($Name, $null, "Process")
        Remove-Item -LiteralPath "Env:\$Name" -ErrorAction SilentlyContinue
    }
    [Environment]::SetEnvironmentVariable("NO_PROXY", "localhost,127.0.0.1,::1", "Process")
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
}

function Normalize-ProcessPathEnvironment {
    $PathValue = $env:Path
    if (-not [string]::IsNullOrWhiteSpace($PathValue)) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
        [Environment]::SetEnvironmentVariable("Path", $PathValue, "Process")
        $env:Path = $PathValue
    }
}

$RetentionCutoff = (Get-Date).AddDays(-14)
Get-ChildItem -LiteralPath $LogDirectory -Filter "notifierr-*.log" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $RetentionCutoff } |
    Remove-Item -Force

$ExistingHealth = Get-NotifierrHealth
if ($ExistingHealth -and $ExistingHealth.ok -eq $true) {
    $ExistingPid = Get-ListeningPid
    Write-LauncherLog "Notifierr is already healthy on ${HostAddress}:$Port pid=$ExistingPid; not starting a duplicate."
    Write-Host "Notifierr is already healthy on ${HostAddress}:$Port pid=$ExistingPid."
    exit 0
}

$OccupiedPid = Get-ListeningPid
if ($OccupiedPid) {
    throw "Port ${HostAddress}:$Port is already occupied by pid=$OccupiedPid, but /health is not healthy. Stop that process or choose another port."
}

Write-LauncherLog "Starting Notifierr from $ProjectRoot on ${HostAddress}:$Port"
Clear-OutboundProxyEnvironment
Normalize-ProcessPathEnvironment

Push-Location -LiteralPath $ProjectRoot
try {
    Write-LauncherLog "Applying Alembic migrations."
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python -m alembic upgrade head >> $LauncherLogPath 2>&1
    $MigrationExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($MigrationExitCode -ne 0) {
        throw "Alembic migration failed with exit code $MigrationExitCode. See $LauncherLogPath."
    }

    $Arguments = @(
        "-m", "uvicorn", "backend.main:app",
        "--host", $HostAddress,
        "--port", [string]$Port,
        "--workers", "1"
    )
    Set-Content -LiteralPath $StdinPath -Value "" -Encoding ASCII
    $Process = Start-Process `
        -FilePath $Python `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardInput $StdinPath `
        -RedirectStandardOutput $StdoutLogPath `
        -RedirectStandardError $StderrLogPath `
        -PassThru

    Write-LauncherLog "Spawned backend pid=$($Process.Id). Waiting for health."
    $Deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    do {
        Start-Sleep -Seconds 1
        if ($Process.HasExited) {
            throw "Backend exited during startup with code $($Process.ExitCode). See $StdoutLogPath and $StderrLogPath."
        }
        $Health = Get-NotifierrHealth
        if ($Health -and $Health.ok -eq $true) {
            $BackendPid = Get-ListeningPid
            if (-not $BackendPid) {
                $BackendPid = $Process.Id
            }
            [ordered]@{
                pid = $BackendPid
                launcher_pid = $Process.Id
                host = $HostAddress
                port = $Port
                started_at = (Get-Date).ToUniversalTime().ToString("o")
                stdout_log = $StdoutLogPath
                stderr_log = $StderrLogPath
                stdin = $StdinPath
                launcher_log = $LauncherLogPath
            } | ConvertTo-Json | Set-Content -LiteralPath $PidPath -Encoding UTF8
            Write-LauncherLog "Backend healthy pid=$BackendPid launcher_pid=$($Process.Id) state=$($Health.state)."
            Write-Host "Notifierr started and is healthy on http://${HostAddress}:$Port pid=$BackendPid."
            exit 0
        }
    } while ((Get-Date) -lt $Deadline)

    throw "Timed out waiting for /health after $HealthTimeoutSeconds seconds. See $StdoutLogPath and $StderrLogPath."
} finally {
    Pop-Location
}
