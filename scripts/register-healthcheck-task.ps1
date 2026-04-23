# Register the ContextPulse health-check as a Windows Scheduled Task.
# Runs every 2 minutes. Pairs with the long-running daemon-watchdog.ps1
# to catch cases where both the watchdog and daemon are stuck.
#
# Idempotent -- re-running replaces the existing task.

param(
    [string]$TaskName = "ContextPulse-HealthCheck",
    [int]$IntervalMinutes = 2,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Unregistered $TaskName"
    } else {
        Write-Host "$TaskName was not registered"
    }
    return
}

$ScriptPath = Join-Path $PSScriptRoot "watchdog-healthcheck.ps1"
if (-not (Test-Path $ScriptPath)) {
    throw "watchdog-healthcheck.ps1 not found at $ScriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "ContextPulse health check: verifies screen_latest.jpg freshness and MCP port 8420, kicks the startup .cmd if either fails. Runs every $IntervalMinutes min."

Write-Host "Registered $TaskName (every $IntervalMinutes min)"
Write-Host "Inspect with: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
