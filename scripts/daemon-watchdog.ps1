# ContextPulse Daemon Watchdog
# Launches the daemon and auto-restarts on crash with exponential backoff.
# Max 5 restarts per rolling hour window. Logs all events.

param(
    [int]$MaxRestartsPerHour = 5,
    [int]$MaxBackoffSeconds = 120,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- Config ---
$VenvPythonw = "C:\Users\david\Projects\ContextPulse\.venv\Scripts\pythonw.exe"
$VenvPython  = "C:\Users\david\Projects\ContextPulse\.venv\Scripts\python.exe"
$Module      = "contextpulse_core.daemon"
$WorkDir     = "C:\Users\david\Projects\ContextPulse"
$LogFile     = "C:\Users\david\screenshots\daemon_watchdog.log"

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
    # Strictly matches "contextpulse_core.daemon" in command line — will NOT touch:
    #   - MCP servers (contextpulse_sight.mcp_server, etc.)
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

# --- Main Loop ---
Write-Log "Watchdog starting (max $MaxRestartsPerHour restarts/hour, max backoff ${MaxBackoffSeconds}s)"

# Kill any zombies from previous crash before first launch
Kill-ZombieDaemons

while ($true) {
    # Check restart budget
    $recentRestarts = Get-RestartsInLastHour
    if ($recentRestarts -ge $MaxRestartsPerHour) {
        Write-Log "Restart budget exhausted ($recentRestarts/$MaxRestartsPerHour in last hour). Sleeping 10 min before retry." "WARN"
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
