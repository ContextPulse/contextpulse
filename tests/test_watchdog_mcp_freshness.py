# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The unified MCP server must not keep serving code older than its source.

Python imports a module once. Start-McpServer relaunches the server only when
its port is dead, and the 4 AM memory guard kills only the daemon, so a fix to
the read path (redaction, search filters) never reached agents until the
watchdog itself was restarted. Measured 2026-11-13: the server was started
2026-11-11 21:30, six hours before the OP_TOKEN redaction rule (8a3918e) was
committed, while the daemon had been restarted after it.

Same harness approach as test_watchdog_stderr_rotation.py: the functions are
lifted out of the SHIPPED .ps1 through the PowerShell AST and run in a real
powershell.exe. Get-CimInstance, Stop-Process, Start-Sleep and Start-McpServer
are replaced by recording stubs, so no real process is touched.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="daemon-watchdog.ps1 is a Windows script"
)

WATCHDOG = Path(__file__).resolve().parents[1] / "scripts" / "daemon-watchdog.ps1"

_HARNESS = r"""
param([string]$WorkDir)
$ErrorActionPreference = "Stop"

$ast = [System.Management.Automation.Language.Parser]::ParseFile('__WATCHDOG__', [ref]$null, [ref]$null)
foreach ($name in @("Get-McpStaleness", "Restart-McpServerIfStale")) {
    $fn = $ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if (-not $fn) { throw "daemon-watchdog.ps1 defines no function $name" }
    Invoke-Expression $fn[0].Extent.Text
}

$McpModule = "contextpulse_core.mcp_unified"
$script:Logged = New-Object System.Collections.ArrayList
$script:Killed = New-Object System.Collections.ArrayList
$script:Started = 0
$script:FakeProcs = @()
$script:CimThrows = $false
function Write-Log { param([string]$Message, [string]$Level = "INFO"); [void]$script:Logged.Add("[$Level] $Message") }
function Get-CimInstance {
    if ($script:CimThrows) { throw "WMI unavailable" }
    return $script:FakeProcs
}
function Stop-Process { param([int]$Id, [switch]$Force, $ErrorAction); [void]$script:Killed.Add($Id) }
function Start-Sleep { param([int]$Seconds) }
function Start-McpServer { $script:Started++ }
function Fake-Proc { param([int]$ProcessId, [string]$CommandLine, [datetime]$CreationDate)
    [pscustomobject]@{ ProcessId = $ProcessId; CommandLine = $CommandLine; CreationDate = $CreationDate } }

__SCENARIO__

@{
    logged  = @($script:Logged)
    killed  = @($script:Killed)
    started = $script:Started
    extra   = $extra
} | ConvertTo-Json -Depth 5 -Compress
"""


def _run(tmp_path: Path, scenario: str) -> dict:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        _HARNESS.replace("__WATCHDOG__", str(WATCHDOG)).replace("__SCENARIO__", scenario),
        encoding="utf-8",
    )
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-WorkDir",
            str(work),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, f"harness failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _source_tree(work: Path, mtime: float) -> None:
    """packages/core/src/pkg/mod.py at `mtime`, plus noise that must be ignored."""
    src = work / "packages" / "core" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    os.utime(src / "mod.py", (mtime, mtime))


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("y = 2\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


# The server "started" at a fixed local time; files are placed an hour either side.
SERVER_STARTED = "2026-11-11 21:30:41"


def _epoch(local: str) -> float:
    return time.mktime(time.strptime(local, "%Y-%m-%d %H:%M:%S"))


STALENESS = """
$s = Get-McpStaleness -StartTimes @([datetime]'2026-11-11 21:30:41', [datetime]'2026-11-11 21:30:42') `
    -SourceRoot (Join-Path $WorkDir 'packages')
$extra = @{ stale = $s.stale; reason = $s.reason; file = $s.newest_file }
"""


def test_a_source_change_after_the_server_started_is_stale(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-12 03:31:47"))
    out = _run(tmp_path, STALENESS)
    assert out["extra"]["stale"] is True
    assert out["extra"]["file"].endswith("mod.py")


def test_a_server_newer_than_every_source_file_is_fresh(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-11 20:30:00"))
    out = _run(tmp_path, STALENESS)
    assert out["extra"]["stale"] is False
    assert out["extra"]["reason"] == "server is newer than every source file"


def test_the_earliest_server_process_decides(tmp_path):
    """Parent and worker start a second apart; a change between them is stale."""
    _source_tree(tmp_path / "work", _epoch("2026-11-11 21:30:41") + 0.5)
    out = _run(tmp_path, STALENESS)
    assert out["extra"]["stale"] is True


def test_tests_and_bytecode_do_not_count_as_source(tmp_path):
    work = tmp_path / "work"
    _source_tree(work, _epoch("2026-11-11 20:30:00"))
    later = _epoch("2026-11-12 09:00:00")
    _touch(work / "packages" / "core" / "tests" / "test_mod.py", later)
    _touch(work / "packages" / "core" / "src" / "pkg" / "__pycache__" / "mod.py", later)
    out = _run(tmp_path, STALENESS)
    assert out["extra"]["stale"] is False


def test_no_source_files_is_an_error_not_a_fresh_verdict(tmp_path):
    """An empty tree must not read as 'nothing changed'."""
    (tmp_path / "work" / "packages").mkdir(parents=True)
    out = _run(
        tmp_path,
        """
try { Get-McpStaleness -StartTimes @([datetime]'2026-11-11 21:30:41') -SourceRoot (Join-Path $WorkDir 'packages') | Out-Null; $extra = 'no error' }
catch { $extra = "threw: $($_.Exception.Message)" }
""",
    )
    assert out["extra"].startswith("threw: no package source files")


RESTART = """
$script:FakeProcs = @(
    (Fake-Proc 33564 'python.exe -m contextpulse_core.mcp_unified --port 8420' ([datetime]'2026-11-11 21:30:41')),
    (Fake-Proc 32380 'python.exe -m contextpulse_core.mcp_unified --port 8420' ([datetime]'2026-11-11 21:30:41')),
    (Fake-Proc 47788 'python.exe -m contextpulse_core.daemon' ([datetime]'2026-11-12 04:45:50'))
)
Restart-McpServerIfStale
$extra = $null
"""


def test_a_stale_server_is_stopped_and_relaunched(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-12 03:31:47"))
    out = _run(tmp_path, RESTART)
    assert sorted(out["killed"]) == [32380, 33564], "only the MCP processes may be stopped"
    assert out["started"] == 1
    assert any("predates" in line and line.startswith("[INFO]") for line in out["logged"])


def test_a_fresh_server_is_left_alone(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-11 20:30:00"))
    out = _run(tmp_path, RESTART)
    assert out["killed"] == []
    assert out["started"] == 0
    assert out["logged"] == []


def test_no_running_server_is_left_to_start_mcpserver(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-12 03:31:47"))
    out = _run(tmp_path, "Restart-McpServerIfStale\n$extra = $null")
    assert out["killed"] == []
    assert out["started"] == 0


def test_a_failed_process_query_is_logged_as_an_error(tmp_path):
    _source_tree(tmp_path / "work", _epoch("2026-11-12 03:31:47"))
    out = _run(tmp_path, "$script:CimThrows = $true\nRestart-McpServerIfStale\n$extra = $null")
    assert out["killed"] == []
    assert any(line.startswith("[ERROR] MCP freshness check failed") for line in out["logged"])


def test_the_supervisor_loop_runs_the_freshness_check_before_start_mcpserver():
    source = WATCHDOG.read_text(encoding="utf-8")
    loop = source[source.index("while ($true) {") :].splitlines()
    calls = [
        line.strip()
        for line in loop
        if line.strip() in ("Restart-McpServerIfStale", "Start-McpServer")
    ]
    assert calls[:2] == ["Restart-McpServerIfStale", "Start-McpServer"], (
        "the freshness check must run before the port check in every iteration"
    )
