# ContextPulse Daemon Watchdog
# Launches the daemon and auto-restarts on crash with exponential backoff.
# Max 5 restarts per rolling hour window. Logs all events.
# Single-instance guard: uses a named mutex to prevent zombie watchdog chains.

param(
    [int]$MaxRestartsPerHour = 5,
    [int]$MaxBackoffSeconds = 120,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- Single-Instance Guard (named mutex) ---
$mutexName = "Global\ContextPulse_DaemonWatchdog_SingleInstance"
$createdNew = $false
try {
    $script:watchdogMutex = [System.Threading.Mutex]::new($true, $mutexName, [ref]$createdNew)
} catch {
    # Mutex already exists and is abandoned or inaccessible - exit
    exit 0
}

if (-not $createdNew) {
    # Another watchdog instance already holds the mutex - exit silently
    $script:watchdogMutex.Dispose()
    exit 0
}

# --- Config ---
$WorkDir       = Split-Path $PSScriptRoot -Parent
$VenvPythonw   = Join-Path $WorkDir ".venv\Scripts\pythonw.exe"
$VenvPython    = Join-Path $WorkDir ".venv\Scripts\python.exe"
$Module        = "contextpulse_core.daemon"
$McpModule     = "contextpulse_core.mcp_unified"
$McpPort       = 8420
$LogFile       = Join-Path $WorkDir "logs\daemon_watchdog.log"
$StderrLog     = Join-Path $WorkDir "daemon_stderr.log"
$StderrBackups = 5   # generations of stderr to retain across restarts

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

function Rotate-StderrLog {
    # Start-Process -RedirectStandardError opens the target file for write
    # (truncating it) on every call, so a crashing run's stderr is
    # destroyed by the very restart that follows it -- there is never a
    # window to read it. Mirrors log_rotation.py's generation-shift naming
    # (x.log -> x.log.1 -> x.log.2 ...) so the PREVIOUS run's stderr
    # survives at daemon_stderr.log.1 after each restart, bounded to
    # $StderrBackups generations (verified empirically 2026-08-21: without
    # this, a second Start-Process to the same -RedirectStandardError path
    # left ONLY the second run's output -- the first run's content was
    # gone, not appended).
    param(
        [Parameter(Mandatory)] [string]$Path,
        [int]$Keep = $StderrBackups
    )
    if (-not (Test-Path $Path)) {
        return
    }
    $oldest = "$Path.$Keep"
    if (Test-Path $oldest) {
        Remove-Item $oldest -Force -ErrorAction SilentlyContinue
    }
    for ($generation = $Keep - 1; $generation -ge 1; $generation--) {
        $source = "$Path.$generation"
        if (Test-Path $source) {
            Move-Item $source "$Path.$($generation + 1)" -Force -ErrorAction SilentlyContinue
        }
    }
    Move-Item $Path "$Path.1" -Force -ErrorAction SilentlyContinue
}

function Get-RestartsInLastHour {
    $cutoff = (Get-Date).AddHours(-1)
    $restartTimestamps.RemoveAll({ param($t) $t -lt $cutoff }) | Out-Null
    return $restartTimestamps.Count
}

function Kill-ZombieDaemons {
    # Kill ONLY leftover ContextPulse daemon processes.
    # Strictly matches "contextpulse_core.daemon" in command line - will NOT touch:
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

function Kill-ZombieMcpServers {
    # Kill leftover stdio MCP server processes from old sessions.
    # Matches "contextpulse_*.mcp_server" but NOT "contextpulse_core.mcp_unified".
    $zombies = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_\w+\.mcp_server" }

    if ($zombies) {
        foreach ($z in $zombies) {
            Write-Log "Killing zombie MCP server (pid=$($z.ProcessId), cmd=$($z.CommandLine))" "WARN"
            Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-McpServer {
    # Ensure the unified MCP server is running on the configured port.
    # Idempotent: returns immediately if the port is already listening, so it
    # is safe to call on every supervisor loop iteration. If the server is NOT
    # up (initial start OR it died since the last check), retry a few times
    # before giving up for this iteration. Previously this ran once at startup
    # with no retry, so a failed launch or a later MCP crash left agents with a
    # dead endpoint until the watchdog was manually restarted.
    param([int]$MaxAttempts = 3)

    $listening = Test-NetConnection -ComputerName 127.0.0.1 -Port $McpPort -WarningAction SilentlyContinue
    if ($listening.TcpTestSucceeded) {
        return
    }

    # Kill any zombie per-session MCP servers from the old stdio era
    Kill-ZombieMcpServers

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Log "Starting unified MCP server (attempt $attempt/$MaxAttempts, -m $McpModule --port $McpPort)"
        $mcpProc = Start-Process -FilePath $VenvPython `
            -ArgumentList "-m", $McpModule, "--port", $McpPort `
            -WorkingDirectory $WorkDir `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardError "$WorkDir\mcp_unified_stderr.log"

        Write-Log "Unified MCP server started (pid=$($mcpProc.Id))"

        # Wait briefly and verify it came up
        Start-Sleep -Seconds 3
        $check = Test-NetConnection -ComputerName 127.0.0.1 -Port $McpPort -WarningAction SilentlyContinue
        if ($check.TcpTestSucceeded) {
            Write-Log "Unified MCP server confirmed healthy on port $McpPort"
            return
        }

        Write-Log "Unified MCP server did not come up (attempt $attempt/$MaxAttempts) - check mcp_unified_stderr.log" "WARN"
        Kill-ZombieMcpServers
        Start-Sleep -Seconds 3
    }

    Write-Log "Unified MCP server FAILED to start after $MaxAttempts attempts - will retry next loop iteration" "ERROR"
}

# --- Main Loop ---
try {
    Write-Log "Watchdog starting (max $MaxRestartsPerHour restarts/hour, max backoff ${MaxBackoffSeconds}s)"

    # Kill any zombies from previous crash before first launch
    Kill-ZombieDaemons

    # Start the unified MCP server (shared across all Claude sessions)
    Start-McpServer

    while ($true) {
        # Re-assert MCP server health every iteration. Start-McpServer no-ops
        # if the port is already listening, so this only acts when the server
        # died. Catches MCP crashes that happen after the initial launch.
        Start-McpServer

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

        # Preserve the PREVIOUS run's stderr before Start-Process truncates
        # the file for this run -- otherwise the crashing run's diagnostics
        # are destroyed by the restart that follows it (verified empirically
        # 2026-08-21: -RedirectStandardError overwrites, never appends).
        Rotate-StderrLog -Path $StderrLog

        # Use python.exe (not pythonw.exe) so stderr is capturable for crash diagnostics.
        # The -WindowStyle Hidden on Start-Process keeps the console window invisible.
        # Redirect stderr to crash log for post-mortem analysis.
        $proc = Start-Process -FilePath $VenvPython `
            -ArgumentList "-m", $Module `
            -WorkingDirectory $WorkDir `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardError $StderrLog

        # Force .NET to associate the process handle NOW, before it can
        # exit. Without this, $proc.ExitCode reliably returns $null after
        # WaitForExit() (verified empirically 2026-08-21 -- reproduced with
        # a plain `cmd /c exit 3` child: $proc.ExitCode was $null every
        # time until .Handle was touched first). This is why every daemon
        # exit was logging "DAEMON CRASHED (code= [0x00000000])" regardless
        # of the real exit code, including clean/graceful exits.
        $null = $proc.Handle

        Write-Log "Daemon started (pid=$($proc.Id))"

        # Wait for process to exit
        $proc.WaitForExit()
        $proc.Refresh()
        $exitCode = $proc.ExitCode
        $runtime = ((Get-Date) - $startTime).TotalSeconds

        # Determine if this was a crash or graceful exit
        if ($exitCode -in $gracefulExitCodes) {
            Write-Log "Daemon exited gracefully (code=$exitCode, runtime=$([math]::Round($runtime))s). Watchdog stopping."
            break
        }

        # It crashed
        Write-Log "Crash diagnostics: see $StderrLog (this run) and $StderrLog.1 (prior run, if any)" "WARN"
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
