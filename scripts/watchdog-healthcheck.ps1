# ContextPulse Health Check -- periodic scheduled task.
# Complements daemon-watchdog.ps1 (the long-running supervisor):
#   - daemon-watchdog.ps1 handles crash-restart of the daemon
#   - the daemon itself (post v0.1.x) supervises its MCP subprocess
#   - THIS script catches the "both the watchdog and daemon are stuck" case
#     and kicks the whole stack by relaunching the startup .cmd.
#
# Checks:
#   1. screen_latest.jpg mtime within $MaxStaleSeconds -- proves Sight is capturing
#   2. port 8420 is listening -- proves the unified MCP server is alive
# If either fails, logs the failure, kills matching zombies, and invokes the
# startup .cmd so the watchdog + daemon + MCP come back up cleanly.
#
# Designed to run every 2 minutes as a Windows Scheduled Task. Exits fast when
# everything is healthy (the common case) so it is nearly free.

param(
    [int]$MaxStaleSeconds = 120,
    [int]$McpPort = 8420,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$WorkDir        = Split-Path $PSScriptRoot -Parent
$ScreenLatest   = Join-Path $env:USERPROFILE "screenshots\screen_latest.jpg"
$StartupCmd     = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ContextPulse.cmd"
$LogFile        = Join-Path $WorkDir "logs\watchdog_healthcheck.log"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    try {
        $logDir = Split-Path $LogFile -Parent
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    } catch {}
}

function Test-ScreenshotFresh {
    if (-not (Test-Path $ScreenLatest)) {
        Write-Log "screen_latest.jpg missing at $ScreenLatest" "WARN"
        return $false
    }
    $age = ((Get-Date) - (Get-Item $ScreenLatest).LastWriteTime).TotalSeconds
    if ($age -gt $MaxStaleSeconds) {
        Write-Log ("screen_latest.jpg stale: {0:N0}s old (threshold {1}s)" -f $age, $MaxStaleSeconds) "WARN"
        return $false
    }
    return $true
}

function Test-McpListening {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect("127.0.0.1", $McpPort, $null, $null)
        $ok  = $iar.AsyncWaitHandle.WaitOne(500)
        if (-not $ok) {
            Write-Log "MCP port $McpPort not listening (connect timeout)" "WARN"
            return $false
        }
        $c.EndConnect($iar) | Out-Null
        return $true
    } catch {
        Write-Log "MCP port $McpPort connect failed: $_" "WARN"
        return $false
    } finally {
        $c.Close()
    }
}

function Invoke-Recovery {
    Write-Log "Recovery triggered -- killing zombies and invoking startup .cmd" "WARN"

    # Kill daemon zombies (same matcher the long-running watchdog uses)
    $zombies = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "contextpulse_core[\.\\/]daemon" -or $_.CommandLine -match "contextpulse_core\.mcp_unified" }

    foreach ($z in $zombies) {
        Write-Log "Killing pid=$($z.ProcessId) cmd=$($z.CommandLine)" "WARN"
        if (-not $DryRun) {
            Stop-Process -Id $z.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    if ($DryRun) {
        Write-Log "[DRY RUN] Would launch $StartupCmd"
        return
    }

    if (-not (Test-Path $StartupCmd)) {
        Write-Log "Startup .cmd not found at $StartupCmd -- cannot relaunch" "ERROR"
        return
    }

    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $StartupCmd -WindowStyle Hidden
    Write-Log "Relaunched via $StartupCmd"
}

# --- Main ---
$screenOk = Test-ScreenshotFresh
$mcpOk    = Test-McpListening

if ($screenOk -and $mcpOk) {
    # Healthy -- log only at debug level (suppressed by default)
    exit 0
}

Write-Log "Health check FAILED (screen=$screenOk, mcp=$mcpOk)" "ERROR"
Invoke-Recovery
exit 1
