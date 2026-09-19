# ContextPulse Watchdog Health Check
# Runs every 2 minutes via the ContextPulse-HealthCheck scheduled task.
# This is the recovery net for the watchdog itself: the watchdog blocks in
# WaitForExit() while the daemon runs, so it can only re-assert MCP health
# AFTER the daemon exits. If the daemon wedges (heartbeat goes stale) or the
# MCP port dies while the daemon is still alive, nothing recovers it until
# this check fires.
#
# Four independent checks (each acts only when something is actually broken):
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
#   4. Daemon session sanity - checks 1-3 above ALL stayed green for 4 days
#      straight (2026-09-15 to 2026-09-19, cp-daemon-session0-blind-capture)
#      while the daemon ran in Windows Session 0 and captured nothing, because
#      none of heartbeat / process-alive / port-listening observe whether the
#      daemon can actually SEE a desktop. This check reads the daemon
#      process's real Win32 session id (Win32_Process.SessionId) -- proof,
#      not a heuristic -- and if it is Session 0, that is treated as broken
#      regardless of what checks 1-3 say. A secondary, advisory-only check
#      also looks at activity.db's last-write time as a cheap "is a capture
#      actually landing" signal; it only ever logs, it never triggers an
#      action, because a quiet desktop overnight looks identical to a broken
#      one from that signal alone.
#
# All actions are targeted: it only ever kills python processes whose command
# line matches contextpulse_core.daemon. It NEVER touches mcp_unified, never
# does a blanket python kill.
#
# This script's OWN session matters too: its recovery path (Start-StartupCmd)
# relaunches the watchdog supervision chain into WHATEVER session this health
# check process is itself running in. That is exactly how the 2026-09-15
# outage started -- the ContextPulse-HealthCheck scheduled task ran with
# LogonType=S4U (Session 0), and its own recovery path then relaunched the
# daemon into Session 0 too, where it stayed for 4 days reporting healthy.
# Start-StartupCmd below refuses to run at all if THIS process is in Session
# 0, and says so loudly, rather than propagating the problem it exists to fix.

$ErrorActionPreference = "Stop"

# --- Config (mirror daemon-watchdog.ps1) ---
$WorkDir       = Split-Path $PSScriptRoot -Parent
$VenvPython    = Join-Path $WorkDir ".venv\Scripts\python.exe"
$WatchdogScript = Join-Path $PSScriptRoot "daemon-watchdog.ps1"
$StartupCmd    = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ContextPulse.cmd"
# Overridable, with a per-user default. Was hardcoded to one developer's home
# directory (cp-watchdog-healthcheck-hardcoded-path), fixed to an env-var
# default -- but the replacement default (%LOCALAPPDATA%\ContextPulse\heartbeat)
# does not match where the daemon actually writes it: daemon.py writes to
# OUTPUT_DIR/heartbeat, where OUTPUT_DIR defaults to %USERPROFILE%\screenshots
# (see packages/core/src/contextpulse_core/config.py, CONTEXTPULSE_OUTPUT_DIR).
# That mismatch silently disabled check 1 entirely: every 2-minute run since
# the first fix logged "Heartbeat missing/unparseable ... no action" because
# nothing was ever written to the path being checked. Mirror config.py's own
# resolution order so the two producers/consumers of this file agree: honor
# CONTEXTPULSE_OUTPUT_DIR if set (same as the daemon), else the same default
# the daemon uses. CONTEXTPULSE_HEARTBEAT remains a direct full-path override
# for cases where the heartbeat genuinely lives somewhere else.
$HeartbeatFile = if ($env:CONTEXTPULSE_HEARTBEAT) { $env:CONTEXTPULSE_HEARTBEAT }
                 elseif ($env:CONTEXTPULSE_OUTPUT_DIR) { Join-Path $env:CONTEXTPULSE_OUTPUT_DIR "heartbeat" }
                 else { Join-Path $env:USERPROFILE "screenshots\heartbeat" }
# Same resolution order as ACTIVITY_DB_PATH in daemon.py -- OUTPUT_DIR (env
# override or the same default) plus CONTEXTPULSE_ACTIVITY_DB (default
# "activity.db"). Used only for the advisory capture-freshness check below.
$ActivityDbFile = if ($env:CONTEXTPULSE_ACTIVITY_DB_PATH) { $env:CONTEXTPULSE_ACTIVITY_DB_PATH }
                  else {
                      $activityDbDir = if ($env:CONTEXTPULSE_OUTPUT_DIR) { $env:CONTEXTPULSE_OUTPUT_DIR }
                                       else { Join-Path $env:USERPROFILE "screenshots" }
                      $activityDbName = if ($env:CONTEXTPULSE_ACTIVITY_DB) { $env:CONTEXTPULSE_ACTIVITY_DB } else { "activity.db" }
                      Join-Path $activityDbDir $activityDbName
                  }
$McpModule     = "contextpulse_core.mcp_unified"
$McpPort       = 8420
$LogFile       = Join-Path $WorkDir "logs\healthcheck.log"

$HeartbeatStaleSeconds = 120
# Generous on purpose: a genuinely idle desktop overnight can go this long
# with no new capture and still be perfectly healthy. This threshold exists
# to catch "capture is structurally broken" (e.g. stuck in Session 0), not
# "David stepped away" -- which is why it only ever logs, never acts.
$ActivityStaleSeconds = 1800

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

function Get-MySessionId {
    # This process's own Windows session id, via the real Win32 session
    # (not a heuristic like an environment variable or a parent-process
    # guess). .NET's Process class exposes it directly, no P/Invoke needed.
    return [System.Diagnostics.Process]::GetCurrentProcess().SessionId
}

function Get-DaemonSessionId {
    # The ContextPulse daemon's real Win32 session id, or $null if no
    # daemon process is found. Win32_Process.SessionId is the same field
    # Task Manager's "Session ID" column reads -- proof, not an inference
    # from heartbeat/port/process-alive, none of which can tell Session 0
    # apart from a healthy interactive session.
    $daemons = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_core[\.\\/]daemon" }
    if (-not $daemons) {
        return $null
    }
    # If there are somehow multiple matches, they should all agree; take
    # the first rather than fail the whole check on an edge case that
    # Kill-WedgedDaemon below already handles by iterating all of them.
    return ($daemons | Select-Object -First 1).SessionId
}

function Get-ActivityDbAgeSeconds {
    # Advisory-only cheap proxy for "is a capture actually landing", via
    # the newer of activity.db and its WAL sidecar (WAL mode means writes
    # land in -wal between checkpoints, so the .db file alone can appear
    # stale even while writes are happening). Returns $null if neither
    # file exists yet (e.g. brand-new install) -- treated as inconclusive,
    # same convention as Get-HeartbeatAgeSeconds.
    $candidates = @($ActivityDbFile, "$ActivityDbFile-wal") | Where-Object { Test-Path $_ }
    if (-not $candidates) {
        return $null
    }
    $newest = ($candidates | ForEach-Object { (Get-Item $_).LastWriteTimeUtc } | Sort-Object -Descending | Select-Object -First 1)
    return ([DateTime]::UtcNow - $newest).TotalSeconds
}

function Get-WatchdogProcesses {
    # The watchdog is a powershell process whose command line invokes
    # daemon-watchdog.ps1. Match on the script name to avoid matching this
    # health-check process.
    return Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "daemon-watchdog\.ps1" }
}

function Test-WatchdogAlive {
    return [bool](Get-WatchdogProcesses)
}

function Test-WatchdogInSession0 {
    # If the watchdog itself is stuck in Session 0, killing a Session-0
    # daemon (see check 4 below) just gets it relaunched right back into
    # Session 0 by that same watchdog -- this is what lets the caller
    # decide to also kick Start-StartupCmd rather than trust the watchdog
    # to self-heal.
    $procs = Get-WatchdogProcesses
    if (-not $procs) {
        return $false
    }
    return [bool]($procs | Where-Object { $_.SessionId -eq 0 })
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
    # Refuse to relaunch the supervision chain into whatever session THIS
    # health-check process is running in if that session is Session 0 --
    # see the file header for why (this is verbatim how the 2026-09-15
    # outage started: a Session-0 health check kicked the Startup launcher,
    # which put the daemon in Session 0 too). Say so LOUDLY: silently
    # declining here would look identical to "nothing was broken", when the
    # actual state is "something is broken AND this check cannot fix it
    # from where it's running."
    $mySessionId = Get-MySessionId
    if ($mySessionId -eq 0) {
        Write-Log "REFUSING to relaunch the watchdog: this health-check process is itself running in Windows Session 0 (non-interactive). Relaunching from here would just push the daemon back into Session 0 -- the exact failure mode this guard exists to stop. The ContextPulse-HealthCheck scheduled task's logon type needs to be interactive, not a service/S4U context." "ERROR"
        return
    }
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
    $daemonSessionId = Get-DaemonSessionId
    $daemonInSession0 = ($null -ne $daemonSessionId) -and ($daemonSessionId -eq 0)

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

    # Check 4: daemon session sanity. This is the check that catches what
    # checks 1-3 structurally cannot: a daemon that is alive, heartbeating,
    # and serving MCP, but running in Windows Session 0 and therefore blind
    # (cp-daemon-session0-blind-capture -- 2,880 consecutive "OK" log lines
    # during a real 4-day outage before this check existed). Acts REGARDLESS
    # of heartbeatStale, because a Session-0 daemon's heartbeat thread runs
    # fine -- staleness alone never catches this.
    if ($daemonInSession0) {
        Write-Log "Daemon process (session=$daemonSessionId) is running in Windows Session 0 (non-interactive) -- it cannot capture screen/voice/input no matter how healthy heartbeat/watchdog/MCP look. Killing it so it can be relaunched into a real session." "ERROR"
        Kill-WedgedDaemon
        if (Test-WatchdogInSession0) {
            Write-Log "The watchdog process supervising it is ALSO in Session 0 -- it would just relaunch the daemon right back into Session 0. Kicking the full supervision chain instead." "ERROR"
            Start-StartupCmd
        }
    }

    # Advisory only, never gates an action: a genuinely idle desktop can go
    # a long time with nothing new to capture and still be perfectly
    # healthy, so this cannot distinguish "broken" from "quiet" on its own.
    # It exists so a human reading the log has the signal available, since
    # the historical failure here was reporting OK while nothing was
    # actually landing.
    $activityAge = Get-ActivityDbAgeSeconds
    if (($null -ne $activityAge) -and ($activityAge -gt $ActivityStaleSeconds)) {
        Write-Log "activity.db has not been written to in $([math]::Round($activityAge / 60))min (threshold $([math]::Round($ActivityStaleSeconds / 60))min) -- captures may not be landing. Not acting on this alone (could be a genuinely idle desktop); see check 4 above for the deterministic signal." "WARN"
    }

    # If everything is healthy, stay silent except for a periodic heartbeat of
    # our own so the log shows the check is actually running.
    if (($null -ne $hbAge) -and (-not $heartbeatStale) -and $watchdogAlive -and (Test-McpAlive) -and (-not $daemonInSession0)) {
        Write-Log "OK (heartbeat $([math]::Round($hbAge))s, watchdog alive, MCP $McpPort listening, daemon session=$daemonSessionId)"
    }
} catch {
    Write-Log "Health check raised an unexpected error: $($_.Exception.Message)" "ERROR"
}
