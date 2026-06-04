# ContextPulse Watchdog Health Check
# Runs every 2 minutes via the ContextPulse-HealthCheck scheduled task.
# This is the recovery net for the watchdog itself: the watchdog blocks in
# WaitForExit() while the daemon runs, so it can only re-assert MCP health
# AFTER the daemon exits. If the daemon wedges (heartbeat goes stale) or the
# MCP port dies while the daemon is still alive, nothing recovers it until
# this check fires.
#
# Three independent checks (each acts only when something is actually broken):
#   1. Heartbeat freshness - the daemon writes an epoch timestamp every ~15s.
#      If it is older than the threshold the daemon is wedged or dead.
#        - watchdog alive  -> kill the wedged daemon so the watchdog's
#                             WaitForExit() returns and it relaunches cleanly.
#        - watchdog dead    -> kick the Startup .cmd to relaunch the watchdog.
#   2. Watchdog process alive - if the supervising powershell process that runs
#      daemon-watchdog.ps1 is gone, kick the Startup .cmd to bring it back.
#   3. MCP port 8420 - if the unified MCP endpoint is down, relaunch it
#      directly. Port-bind dedupe makes a redundant launch a no-op, so this is
#      safe even if the watchdog also relaunches it on its next loop.
#
# All actions are targeted: it only ever kills python processes whose command
# line matches contextpulse_core.daemon. It NEVER touches mcp_unified, never
# does a blanket python kill.

$ErrorActionPreference = "Stop"

# --- Config (mirror daemon-watchdog.ps1) ---
$WorkDir       = Split-Path $PSScriptRoot -Parent
$VenvPython    = Join-Path $WorkDir ".venv\Scripts\python.exe"
$WatchdogScript = Join-Path $PSScriptRoot "daemon-watchdog.ps1"
$StartupCmd    = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ContextPulse.cmd"
$HeartbeatFile = "C:\Users\david\screenshots\heartbeat"
$McpModule     = "contextpulse_core.mcp_unified"
$McpPort       = 8420
$LogFile       = Join-Path $WorkDir "logs\healthcheck.log"

$HeartbeatStaleSeconds = 120

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Get-HeartbeatAgeSeconds {
    # Returns the age of the daemon heartbeat in seconds, or $null if the file
    # is missing or unparseable (treat unparseable as "no heartbeat").
    if (-not (Test-Path $HeartbeatFile)) {
        return $null
    }
    try {
        $raw = (Get-Content -Path $HeartbeatFile -Raw -ErrorAction Stop).Trim()
        $beat = [double]$raw
    } catch {
        return $null
    }
    $now = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
    return $now - $beat
}

function Test-WatchdogAlive {
    # The watchdog is a powershell process whose command line invokes
    # daemon-watchdog.ps1. Match on the script name to avoid matching this
    # health-check process.
    $procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "daemon-watchdog\.ps1" }
    return [bool]$procs
}

function Test-McpAlive {
    $listening = Test-NetConnection -ComputerName 127.0.0.1 -Port $McpPort -WarningAction SilentlyContinue
    return $listening.TcpTestSucceeded
}

function Kill-WedgedDaemon {
    # Kill ONLY the ContextPulse daemon (matches contextpulse_core.daemon).
    # Will NOT touch mcp_unified, monitor hotkeys, or any other python process.
    $daemons = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_core[\.\\/]daemon" }
    if (-not $daemons) {
        Write-Log "Heartbeat stale but no daemon process found to kill - watchdog should relaunch on its own." "WARN"
        return
    }
    foreach ($d in $daemons) {
        Write-Log "Killing wedged daemon (pid=$($d.ProcessId)) so the watchdog WaitForExit returns and relaunches it." "WARN"
        Stop-Process -Id $d.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Start-StartupCmd {
    if (-not (Test-Path $StartupCmd)) {
        Write-Log "Startup launcher not found at $StartupCmd - cannot relaunch watchdog." "ERROR"
        return
    }
    Write-Log "Kicking Startup launcher to relaunch the watchdog: $StartupCmd" "WARN"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$StartupCmd`"" -WindowStyle Hidden
}

function Start-McpDirect {
    Write-Log "MCP port $McpPort is down - relaunching MCP server directly (-m $McpModule --port $McpPort)." "WARN"
    Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", $McpModule, "--port", $McpPort `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden `
        -RedirectStandardError "$WorkDir\mcp_unified_stderr.log"
}

# --- Main ---
try {
    $watchdogAlive = Test-WatchdogAlive
    $hbAge = Get-HeartbeatAgeSeconds

    # Check 1 + 2: daemon liveness via heartbeat, and the watchdog process.
    # Only act on a PARSEABLE-but-old heartbeat. A missing/unparseable value
    # ($hbAge -eq $null) is treated as inconclusive (likely a transient read
    # race mid-write) and is NOT grounds to kill - that avoids a false-positive
    # kill loop against a healthy daemon. A genuinely dead daemon leaves an old
    # but parseable timestamp, which is the case we want to act on.
    $heartbeatStale = ($null -ne $hbAge) -and ($hbAge -gt $HeartbeatStaleSeconds)

    if ($null -eq $hbAge) {
        Write-Log "Heartbeat missing/unparseable this cycle - treating as inconclusive, no action. watchdogAlive=$watchdogAlive" "WARN"
    }

    if ($heartbeatStale) {
        Write-Log "Daemon heartbeat is stale ($([math]::Round($hbAge))s old, threshold ${HeartbeatStaleSeconds}s). watchdogAlive=$watchdogAlive" "WARN"
        if ($watchdogAlive) {
            # Watchdog is supervising but the daemon is wedged. Kill the daemon;
            # the watchdog's WaitForExit() will return and relaunch it.
            Kill-WedgedDaemon
        } else {
            # No watchdog at all - bring the whole supervision chain back.
            Start-StartupCmd
        }
    } elseif (-not $watchdogAlive) {
        # Daemon heartbeat is fresh but the watchdog process is gone (e.g. it
        # crashed or was killed while the daemon kept running). Relaunch the
        # watchdog so future crashes are supervised again. The watchdog's
        # single-instance mutex + zombie-kill makes this safe.
        Write-Log "Heartbeat is fresh but the watchdog process is missing - relaunching watchdog." "WARN"
        Start-StartupCmd
    }

    # Check 3: MCP endpoint. Independent of the daemon - relaunch directly if
    # down, since the watchdog only re-checks MCP after the daemon exits.
    if (-not (Test-McpAlive)) {
        Start-McpDirect
    }

    # If everything is healthy, stay silent except for a periodic heartbeat of
    # our own so the log shows the check is actually running.
    if (($null -ne $hbAge) -and (-not $heartbeatStale) -and $watchdogAlive -and (Test-McpAlive)) {
        Write-Log "OK (heartbeat $([math]::Round($hbAge))s, watchdog alive, MCP $McpPort listening)"
    }
} catch {
    Write-Log "Health check raised an unexpected error: $($_.Exception.Message)" "ERROR"
}
