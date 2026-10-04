[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
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
$ServiceLogPath = Join-Path $LogDirectory ("notifierr-service-{0}.log" -f $DateStamp)

function Write-ServiceLog {
    param([string]$Message)
    $Line = "{0} {1}" -f (Get-Date -Format "o"), $Message
    Add-Content -LiteralPath $ServiceLogPath -Value $Line
}

function Import-DotEnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($RawLine in Get-Content -LiteralPath $Path) {
        $Line = $RawLine.Trim()
        if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
            continue
        }

        $Key, $Value = $Line.Split("=", 2)
        $Key = $Key.Trim()
        if (-not ($Key -match "^[A-Za-z_][A-Za-z0-9_]*$")) {
            continue
        }

        if ([Environment]::GetEnvironmentVariable($Key, "Process")) {
            continue
        }

        $Value = $Value.Trim()
        if (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'"))) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($Key, $Value, "Process")
    }
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

$RetentionCutoff = (Get-Date).AddDays(-14)
Get-ChildItem -LiteralPath $LogDirectory -Filter "notifierr-*.log" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $RetentionCutoff } |
    Remove-Item -Force

$ExistingHealth = Get-NotifierrHealth
if ($ExistingHealth -and $ExistingHealth.ok -eq $true) {
    $ExistingPid = Get-ListeningPid
    Write-ServiceLog "Notifierr is already healthy on ${HostAddress}:$Port pid=$ExistingPid; foreground service runner will not start a duplicate."
    exit 0
}

$OccupiedPid = Get-ListeningPid
if ($OccupiedPid) {
    Write-ServiceLog "Port ${HostAddress}:$Port is occupied by pid=$OccupiedPid and /health is not healthy."
    throw "Port ${HostAddress}:$Port is already occupied by pid=$OccupiedPid, but /health is not healthy."
}

Set-Location -LiteralPath $ProjectRoot
Import-DotEnvFile -Path (Join-Path $ProjectRoot ".env")
Import-DotEnvFile -Path (Join-Path $ProjectRoot "backend\.env")
Clear-OutboundProxyEnvironment
Normalize-ProcessPathEnvironment

Write-ServiceLog "Applying Alembic migrations from $ProjectRoot."
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -m alembic upgrade head >> $ServiceLogPath 2>&1
$MigrationExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference
if ($MigrationExitCode -ne 0) {
    Write-ServiceLog "Alembic migration failed exit_code=$MigrationExitCode."
    exit $MigrationExitCode
}

Write-ServiceLog "Starting foreground Uvicorn backend on ${HostAddress}:$Port."
while ($true) {
    $BackendStartedAt = Get-Date
    $ErrorActionPreference = "Continue"
    & $Python -m uvicorn backend.main:app --host $HostAddress --port $Port --workers 1 >> $ServiceLogPath 2>&1
    $BackendExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    $RuntimeSeconds = [int]((Get-Date) - $BackendStartedAt).TotalSeconds
    Write-ServiceLog "Foreground backend exited exit_code=$BackendExitCode runtime_seconds=$RuntimeSeconds."
    if ($BackendExitCode -eq 0) {
        exit 0
    }
    if ($RuntimeSeconds -lt 10) {
        Write-ServiceLog "Backend exited before startup grace elapsed; returning failure to Task Scheduler."
        exit 1
    }
    Write-ServiceLog "Restarting foreground backend in 5 seconds inside the single scheduled task."
    Start-Sleep -Seconds 5
}
