# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""R2-4. daemon_stderr.log had no size bound in either direction.

It was rotated only at restart, and the five generations it kept were each
unbounded -- daemon_stderr.log.2 reached 20MB once. The paste-path breadcrumbs
write to this file on every dictation, so it is the log family most likely to
grow.

The live file genuinely cannot be rotated: Start-Process
-RedirectStandardError hands the child an inherited handle that shares neither
delete nor rename, and Move-Item on it fails with "the process cannot access
the file" (measured against a live child 2026-09-19; SetLength(0) through a
FileShare.ReadWrite handle succeeds and buys nothing, because the writer's file
pointer is untouched and the length returns on the next write). So the shipped
code reports the live file and bounds the retained generations, and these tests
check exactly that split.

The harness lifts the functions out of the SHIPPED .ps1 through the PowerShell
AST, the way the healthcheck rotation in a35f221 was verified, and runs them in
a real powershell.exe. Running the script itself is not an option: it would
take the watchdog's single-instance mutex, kill "zombie" daemons and start the
supervisor loop.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="daemon-watchdog.ps1 is a Windows script"
)

WATCHDOG = Path(__file__).resolve().parents[1] / "scripts" / "daemon-watchdog.ps1"

LIFTED_FUNCTIONS = (
    "Move-LogGeneration",
    "Limit-StderrLogSize",
    "Test-StderrLogSize",
    "Rotate-StderrLog",
)
LIFTED_VARIABLES = (
    "StderrBackups",
    "StderrMaxBytes",
    "StderrMaxTotalBytes",
    "StderrCheckSeconds",
    "StderrRotateMutexName",
)

# Parses the shipped file, re-defines the named functions and the top-level
# variables they close over, and stubs Write-Log so every line the rotation
# emits is captured instead of appended to a real log. $Scenario then runs.
_HARNESS = r"""
param([string]$WorkDir)
$ErrorActionPreference = "Stop"

$src = '__WATCHDOG__'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$null, [ref]$null)

foreach ($name in @(__FUNCTIONS__)) {
    $fn = $ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if (-not $fn) { throw "daemon-watchdog.ps1 defines no function $name" }
    Invoke-Expression $fn[0].Extent.Text
}

$wantedVars = @(__VARIABLES__)
$found = @()
foreach ($a in $ast.FindAll({
    param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst]
}, $true)) {
    $left = $a.Left
    if ($left -is [System.Management.Automation.Language.VariableExpressionAst] -and
        $wantedVars -contains $left.VariablePath.UserPath) {
        Invoke-Expression $a.Extent.Text
        $found += $left.VariablePath.UserPath
    }
}
foreach ($v in $wantedVars) {
    if ($found -notcontains $v) { throw "daemon-watchdog.ps1 declares no `$$v" }
}

$script:StderrOversizeReported = $false
$script:Logged = New-Object System.Collections.ArrayList
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    [void]$script:Logged.Add("[$Level] $Message")
}

$StderrLog = Join-Path $WorkDir "daemon_stderr.log"
function Set-Log {
    param([string]$Name, [int]$Bytes)
    Set-Content -Path (Join-Path $WorkDir $Name) -Value ("x" * $Bytes) -NoNewline -Encoding ascii
}
function Get-Sizes {
    $sizes = @{}
    foreach ($f in Get-ChildItem $WorkDir -File) { $sizes[$f.Name] = $f.Length }
    return $sizes
}

__SCENARIO__

@{
    logged = @($script:Logged)
    sizes  = Get-Sizes
    extra  = $extra
} | ConvertTo-Json -Depth 5 -Compress
"""


def _run_scenario(tmp_path: Path, scenario: str) -> dict:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    script = (
        _HARNESS.replace("__WATCHDOG__", str(WATCHDOG))
        .replace("__FUNCTIONS__", ", ".join(f'"{n}"' for n in LIFTED_FUNCTIONS))
        .replace("__VARIABLES__", ", ".join(f'"{n}"' for n in LIFTED_VARIABLES))
        .replace("__SCENARIO__", scenario)
    )
    harness = tmp_path / "harness.ps1"
    harness.write_text(script, encoding="utf-8")

    powershell = shutil.which("powershell.exe") or "powershell.exe"
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(harness), "-WorkDir", str(work)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"harness failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_shipped_file_declares_the_size_bounds():
    """The defaults are part of the fix, so they are asserted against the
    shipped text rather than re-stated in the harness."""
    source = WATCHDOG.read_text(encoding="utf-8")
    assert re.search(r"^\$StderrMaxBytes\s*=\s*5MB", source, re.M), (
        "daemon-watchdog.ps1 declares no $StderrMaxBytes"
    )
    assert re.search(r"^\$StderrMaxTotalBytes\s*=\s*25MB", source, re.M), (
        "daemon-watchdog.ps1 declares no $StderrMaxTotalBytes"
    )
    assert re.search(r'^\$StderrRotateMutexName\s*=\s*"Local\\', source, re.M), (
        "the rotate mutex must be Local\\ -- Global\\ needs SeCreateGlobalPrivilege"
    )


def test_the_supervisor_no_longer_blocks_forever_on_waitforexit():
    """A WaitForExit() with no timeout parks the only code that could look at
    the stderr log for the daemon's entire lifetime."""
    source = WATCHDOG.read_text(encoding="utf-8")
    assert "$proc.WaitForExit()" not in source, (
        "the supervisor still blocks with an untimed WaitForExit()"
    )
    assert "Test-StderrLogSize -Path $StderrLog" in source


def test_rotation_shifts_generations_and_prunes_by_total_bytes(tmp_path):
    """The size bound that can actually be enforced: nothing holds the retained
    generations open, so they are capped by total bytes, oldest dropped first."""
    result = _run_scenario(tmp_path, """
Set-Log "daemon_stderr.log" 100
Set-Log "daemon_stderr.log.1" 200
Set-Log "daemon_stderr.log.2" 200
Rotate-StderrLog -Path $StderrLog -Keep 5
$extra = $null
""")
    sizes = result["sizes"]
    assert "daemon_stderr.log" not in sizes, "the live file was not rotated away"
    assert sizes.get("daemon_stderr.log.1") == 100
    assert sizes.get("daemon_stderr.log.2") == 200
    assert sizes.get("daemon_stderr.log.3") == 200


def test_the_oldest_generation_is_dropped_when_the_total_is_over_the_cap(tmp_path):
    result = _run_scenario(tmp_path, """
$StderrMaxTotalBytes = 300
Set-Log "daemon_stderr.log" 100
Set-Log "daemon_stderr.log.1" 200
Set-Log "daemon_stderr.log.2" 200
Rotate-StderrLog -Path $StderrLog -Keep 5
$extra = $null
""")
    sizes = result["sizes"]
    assert "daemon_stderr.log.3" not in sizes, (
        f"the oldest generation survived an over-cap total: {sizes}"
    )
    assert sizes.get("daemon_stderr.log.1") == 100, "the newest generation was dropped"
    assert sizes.get("daemon_stderr.log.2") == 200
    assert any("dropped" in line and "daemon_stderr.log.3" in line
               for line in result["logged"]), result["logged"]


def test_a_move_that_fails_is_reported_not_swallowed(tmp_path):
    """-ErrorAction SilentlyContinue here is not cosmetic: the caller goes
    straight on to Start-Process -RedirectStandardError, which TRUNCATES the
    file, so a silent rotation failure destroys the crash diagnostics the
    rotation exists to preserve and reads as a success."""
    result = _run_scenario(tmp_path, """
Set-Log "daemon_stderr.log" 100
$locked = Join-Path $WorkDir "daemon_stderr.log.1"
Set-Log "daemon_stderr.log.1" 50
$handle = [System.IO.File]::Open($locked, [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    Rotate-StderrLog -Path $StderrLog -Keep 5
} finally {
    $handle.Close()
}
$extra = $null
""")
    assert any("could not move" in line.lower() for line in result["logged"]), (
        f"a failed move was swallowed: {result['logged']}"
    )


def test_a_failed_base_move_is_escalated_to_error(tmp_path):
    """The base move failing is the case that loses the crash evidence."""
    result = _run_scenario(tmp_path, """
Set-Log "daemon_stderr.log" 100
$handle = [System.IO.File]::Open($StderrLog, [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    Rotate-StderrLog -Path $StderrLog -Keep 5
} finally {
    $handle.Close()
}
$extra = $null
""")
    assert any(line.startswith("[ERROR]") and "truncate" in line
               for line in result["logged"]), result["logged"]


def test_a_separate_process_holding_the_mutex_makes_this_run_decline(tmp_path):
    """A Windows mutex is owned by a THREAD and is re-entrant, so a same-thread
    test would pass trivially. The holder has to be another process."""
    result = _run_scenario(tmp_path, """
Set-Log "daemon_stderr.log" 100
$holderScript = Join-Path $WorkDir "holder.ps1"
$holderLines = @(
    ('$m = [System.Threading.Mutex]::new($false, "' + $StderrRotateMutexName + '")'),
    '[void]$m.WaitOne(5000)',
    'Write-Output "HELD"',
    'Start-Sleep -Seconds 12',
    '$m.ReleaseMutex()'
)
Set-Content -Path $holderScript -Encoding ascii -Value ($holderLines -join "`r`n")
$holderOut = Join-Path $WorkDir "holder.out"
$holder = Start-Process -FilePath "powershell.exe" -PassThru -WindowStyle Hidden `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $holderScript `
    -RedirectStandardOutput $holderOut -RedirectStandardError (Join-Path $WorkDir "holder.err")
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path $holderOut) -and ((Get-Content $holderOut -Raw) -match "HELD")) { break }
    Start-Sleep -Milliseconds 200
}
$extra = @{
    held_signalled = [bool]((Test-Path $holderOut) -and ((Get-Content $holderOut -Raw) -match "HELD"))
    holder_alive   = [bool](-not $holder.HasExited)
    holder_err     = [string](Get-Content (Join-Path $WorkDir "holder.err") -Raw -ErrorAction SilentlyContinue)
}
try {
    Rotate-StderrLog -Path $StderrLog -Keep 5
} finally {
    Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
}
""")
    assert result["extra"]["held_signalled"], (
        f"the holder process never took the mutex: {result['extra']}"
    )
    assert any("mutex" in line.lower() for line in result["logged"]), result["logged"]
    assert "daemon_stderr.log" in result["sizes"], (
        "the run rotated anyway while another process held the mutex"
    )


def test_an_oversize_live_log_is_reported_once_per_run(tmp_path):
    """The live file cannot be rotated under its own writer, so this is a
    report, and it must not repeat once a minute for as long as the daemon
    runs."""
    result = _run_scenario(tmp_path, """
$StderrMaxBytes = 100
Set-Log "daemon_stderr.log" 500
Test-StderrLogSize -Path $StderrLog
Test-StderrLogSize -Path $StderrLog
Test-StderrLogSize -Path $StderrLog
$extra = $null
""")
    oversize = [ln for ln in result["logged"] if "cannot be rotated while" in ln]
    assert len(oversize) == 1, result["logged"]
    assert "500 bytes" in oversize[0]


def test_a_small_live_log_says_nothing(tmp_path):
    result = _run_scenario(tmp_path, """
$StderrMaxBytes = 10000
Set-Log "daemon_stderr.log" 500
Test-StderrLogSize -Path $StderrLog
$extra = $null
""")
    assert result["logged"] == [], result["logged"]


def test_rotation_clears_the_oversize_report_for_the_next_run(tmp_path):
    """Otherwise the warning fires once ever, not once per daemon run."""
    result = _run_scenario(tmp_path, """
$StderrMaxBytes = 100
Set-Log "daemon_stderr.log" 500
Test-StderrLogSize -Path $StderrLog
Rotate-StderrLog -Path $StderrLog -Keep 5
Set-Log "daemon_stderr.log" 500
Test-StderrLogSize -Path $StderrLog
$extra = $null
""")
    oversize = [ln for ln in result["logged"] if "cannot be rotated while" in ln]
    assert len(oversize) == 2, result["logged"]
