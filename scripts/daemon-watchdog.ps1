# ContextPulse Daemon Watchdog
# Launches the daemon and auto-restarts on crash with exponential backoff.
# Max 5 restarts per rolling hour window. Logs all events.
# Single-instance guard: uses a named mutex to prevent zombie watchdog chains.
#
# Scope: this watchdog OWNS the daemon's lifecycle only.
# The unified MCP server is supervised by the daemon itself (see
# ContextPulseDaemon._supervise_mcp in packages/core/src/contextpulse_core/daemon.py).
# If the daemon is alive, the daemon keeps MCP alive. If the daemon crashes,
# this watchdog restarts the daemon and the daemon starts a fresh MCP.
# A separate periodic scheduled task (scripts/watchdog-healthcheck.ps1)
# catches the rare case where both this watchdog and the daemon are stuck.

param(
    [int]$MaxRestartsPerHour = 5,
    [int]$MaxBackoffSeconds = 120,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- Single-Instance Guard (named mutex) ---
# Only exit when a LIVE watchdog holds the mutex. An *abandoned* mutex
# (previous owner died uncleanly) is recoverable -- we take ownership.
# Previously this script exited silently on AbandonedMutexException, which
# meant after any ungraceful crash of the watchdog no new watchdog could
# ever start until reboot. See commit for full root-cause notes.
$mutexName = "Global\ContextPulse_DaemonWatchdog_SingleInstance"
$createdNew = $false
$script:watchdogMutex = [System.Threading.Mutex]::new($false, $mutexName, [ref]$createdNew)

try {
    # Wait up to 1s to acquire. If a live watchdog holds it, this times out.
    $acquired = $script:watchdogMutex.WaitOne(1000)
} catch [System.Threading.AbandonedMutexException] {
    # Previous owner died without releasing -- WaitOne still grants us
    # ownership. Proceed normally.
    $acquired = $true
}

if (-not $acquired) {
    # A live watchdog already holds the mutex -- exit silently
    $script:watchdogMutex.Dispose()
    exit 0
}

# --- Config ---
$WorkDir     = Split-Path $PSScriptRoot -Parent
$VenvPythonw = Join-Path $WorkDir ".venv\Scripts\pythonw.exe"
$VenvPython  = Join-Path $WorkDir ".venv\Scripts\python.exe"
$Module      = "contextpulse_core.daemon"
$LogFile     = Join-Path $WorkDir "logs\daemon_watchdog.log"

# --- State ---
$restartTimestamps = [System.Collections.Generic.List[datetime]]::new()
$backoffSeconds = 5
$gracefulExitCodes = @(0, -1073741510)  # 0 = clean exit, 0xC000013A = Ctrl+C

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Get-RestartsInLastHour {
    $cutoff = (Get-Date).AddHours(-1)
    $restartTimestamps.RemoveAll({ param($t) $t -lt $cutoff }) | Out-Null
    return $restartTimestamps.Count
}

function Kill-ZombieDaemons {
    # Kill ONLY leftover ContextPulse daemon processes.
    # Strictly matches "contextpulse_core.daemon" in command line -- will NOT touch:
    #   - monitor-hotkeys.pyw
    #   - Any other pythonw/python process
    $zombies = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_core[\.\\/]daemon" }

    if ($zombies) {
        foreach ($z in $zombies) {
            Write-Log "Killing zombie daemon (pid=$($z.ProcessId), cmd=$($z.CommandLine))" "WARN"
            Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
}

function Kill-StaleStdioMcpServers {
    # Kill leftover stdio MCP server processes from the pre-unified era.
    # These are orphans from old Claude Code sessions that spawned per-module
    # stdio servers. The unified HTTP server on port 8420 is supervised by
    # the daemon itself and is NOT matched here.
    $zombies = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_\w+\.mcp_server" }

    if ($zombies) {
        foreach ($z in $zombies) {
            Write-Log "Killing stale stdio MCP server (pid=$($z.ProcessId), cmd=$($z.CommandLine))" "WARN"
            Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- Main Loop ---
try {
    Write-Log "Watchdog starting (max $MaxRestartsPerHour restarts/hour, max backoff ${MaxBackoffSeconds}s)"

    # Kill any zombies from previous crash before first launch
    Kill-ZombieDaemons

    # One-shot cleanup: kill pre-unified stdio MCP orphans. The unified
    # MCP server itself is supervised by the daemon (not here).
    Kill-StaleStdioMcpServers

    while ($true) {
        # Check restart budget
        $recentRestarts = Get-RestartsInLastHour
        if ($recentRestarts -ge $MaxRestartsPerHour) {
            Write-Log "Restart budget exhausted ($recentRestarts/$($MaxRestartsPerHour) in last hour). Sleeping 10 min before retry." "WARN"
            Start-Sleep -Seconds 600
            continue
        }

        # Kill zombies before each restart (crashed process may have left orphan threads)
        if ($restartTimestamps.Count -gt 0) {
            Kill-ZombieDaemons
        }

        # Launch daemon
        Write-Log "Launching ContextPulse daemon (python.exe -m $Module)"
        $startTime = Get-Date

        if ($DryRun) {
            Write-Log "[DRY RUN] Would launch daemon. Exiting."
            return
        }

        # Use python.exe (not pythonw.exe) so stderr is capturable for crash diagnostics.
        # The -WindowStyle Hidden on Start-Process keeps the console window invisible.
        # Redirect stderr to crash log for post-mortem analysis.
        $proc = Start-Process -FilePath $VenvPython `
            -ArgumentList "-m", $Module `
            -WorkingDirectory $WorkDir `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardError "$WorkDir\daemon_stderr.log"

        Write-Log "Daemon started (pid=$($proc.Id))"

        # Wait for process to exit
        $proc.WaitForExit()
        $exitCode = $proc.ExitCode
        $runtime = ((Get-Date) - $startTime).TotalSeconds

        # Determine if this was a crash or graceful exit
        if ($exitCode -in $gracefulExitCodes) {
            Write-Log "Daemon exited gracefully (code=$exitCode, runtime=$([math]::Round($runtime))s). Watchdog stopping."
            break
        }

        # It crashed
        $exitHex = "0x{0:X8}" -f [uint32]$exitCode
        Write-Log "DAEMON CRASHED (code=$exitCode [$exitHex], runtime=$([math]::Round($runtime))s)" "ERROR"

        # Record restart timestamp
        $restartTimestamps.Add((Get-Date))

        # Reset backoff if daemon ran >5 min (it was stable, not a boot-loop)
        if ($runtime -gt 300) {
            $backoffSeconds = 5
        }

        Write-Log "Restarting in ${backoffSeconds}s (restart $($restartTimestamps.Count)/$MaxRestartsPerHour in last hour)"
        Start-Sleep -Seconds $backoffSeconds

        # Exponential backoff (2, 4, 8, 16... capped)
        $backoffSeconds = [math]::Min($backoffSeconds * 2, $MaxBackoffSeconds)
    }

    Write-Log "Watchdog exiting."
} finally {
    # Release the mutex so a new watchdog can start if needed
    if ($script:watchdogMutex) {
        try { $script:watchdogMutex.ReleaseMutex() } catch {}
        $script:watchdogMutex.Dispose()
    }
}
