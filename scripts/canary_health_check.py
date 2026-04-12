# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""ContextPulse Canary Health Check

Calls every MCP tool with minimal arguments and reports pass/fail status.
Designed for cron/Task Scheduler to catch regressions automatically.

Usage:
  python scripts/canary_health_check.py              # human-readable summary
  python scripts/canary_health_check.py --json       # JSON to stdout
  python scripts/canary_health_check.py --verbose    # show each tool as it runs
  python scripts/canary_health_check.py --no-start   # skip daemon auto-start

Exit codes:
  0 — all tools passed
  1 — one or more tools failed

Logs: results are always appended to logs/canary_results.json (last 100 runs).
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure packages are importable when run standalone (editable installs
# cover this in dev, but belt-and-suspenders for cron/CI).
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _pkg_dir in (_PROJECT_ROOT / "packages").iterdir():
    _src = _pkg_dir / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

_LOGS_DIR = _PROJECT_ROOT / "logs"
_LOG_FILE = _LOGS_DIR / "canary_results.json"
_MAX_STORED_RUNS = 100


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    server: str
    tool: str
    passed: bool
    duration_ms: float
    error: str = ""
    return_preview: str = ""


@dataclass
class HealthReport:
    timestamp: str = ""
    results: list = field(default_factory=list)
    daemon_alive: bool = False
    heartbeat_age_s: float = -1.0
    daemon_started: bool = False  # True if we launched it this run

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def all_healthy(self) -> bool:
        return self.failed == 0


# ---------------------------------------------------------------------------
# Daemon helpers
# ---------------------------------------------------------------------------

def _output_dir() -> Path:
    return Path(os.environ.get(
        "CONTEXTPULSE_OUTPUT_DIR", str(Path.home() / "screenshots")
    ))


def check_daemon_heartbeat() -> tuple[bool, float]:
    """Return (alive, age_seconds). alive=True when heartbeat < 60 s old."""
    heartbeat = _output_dir() / "heartbeat"
    if not heartbeat.exists():
        return False, -1.0
    try:
        age = time.time() - float(heartbeat.read_text(encoding="utf-8").strip())
        return age < 60, round(age, 1)
    except Exception:
        return False, -1.0


def start_daemon_if_needed() -> bool:
    """Attempt to launch the ContextPulse daemon in the background.

    Returns True if we actually started it (it wasn't already alive).
    The daemon is a tray-icon process; we fire-and-forget and wait up to
    8 seconds for the heartbeat file to appear.
    """
    alive, _ = check_daemon_heartbeat()
    if alive:
        return False  # already running

    # Try the installed entry-point first, fall back to the module
    candidates = [
        ["contextpulse-daemon"],
        [sys.executable, "-m", "contextpulse_core.daemon"],
    ]
    launched = False
    for cmd in candidates:
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )
            launched = True
            break
        except (FileNotFoundError, OSError):
            continue

    if not launched:
        return False

    # Wait up to 8 s for heartbeat
    for _ in range(16):
        time.sleep(0.5)
        alive, _ = check_daemon_heartbeat()
        if alive:
            return True
    return False  # launched but heartbeat didn't appear in time


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

def _call_tool(server: str, tool_name: str, func, kwargs: dict) -> ToolResult:
    t0 = time.perf_counter()
    try:
        result = func(**kwargs)
        duration = (time.perf_counter() - t0) * 1000
        if isinstance(result, str):
            preview = result[:200]
        elif isinstance(result, list):
            preview = f"[list of {len(result)} items]"
        elif result is None:
            preview = "None"
        else:
            preview = str(result)[:200]
        return ToolResult(server, tool_name, True, round(duration, 1), return_preview=preview)
    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        return ToolResult(server, tool_name, False, round(duration, 1),
                          error=f"{type(exc).__name__}: {exc}")


def _failing(exc: Exception):
    """Return a zero-arg callable that re-raises the given exception."""
    def _f():
        raise exc
    return _f


# ---------------------------------------------------------------------------
# Tool registry — all 26 MCP tools across 5 servers
# ---------------------------------------------------------------------------

def _build_checks() -> list[tuple[str, str, object, dict]]:
    """Return list of (server, tool_name, callable, kwargs) for every tool."""
    checks: list[tuple[str, str, object, dict]] = []

    # ── Sight (screen) — 12 tools ─────────────────────────────────────────
    try:
        from contextpulse_sight import mcp_server as sight  # type: ignore
        checks += [
            ("sight", "get_screenshot",       sight.get_screenshot,       {"mode": "active"}),
            ("sight", "get_recent",            sight.get_recent,            {"count": 1, "seconds": 10}),
            ("sight", "get_screen_text",       sight.get_screen_text,       {}),
            ("sight", "get_buffer_status",     sight.get_buffer_status,     {}),
            ("sight", "get_activity_summary",  sight.get_activity_summary,  {"hours": 0.01}),
            ("sight", "search_history",        sight.search_history,        {"query": "__canary__", "minutes_ago": 1}),
            ("sight", "get_context_at",        sight.get_context_at,        {"minutes_ago": 0.1}),
            ("sight", "get_clipboard_history", sight.get_clipboard_history, {"count": 1}),
            ("sight", "search_clipboard",      sight.search_clipboard,      {"query": "__canary__", "minutes_ago": 1}),
            ("sight", "get_agent_stats",       sight.get_agent_stats,       {"hours": 0.01}),
            # Pro-gated tools — canary tests reachability; @_require_pro will
            # return an "upgrade required" string rather than raising.
            ("sight", "search_all_events",     sight.search_all_events,     {"query": "__canary__", "minutes_ago": 1}),
            ("sight", "get_event_timeline",    sight.get_event_timeline,    {"minutes_ago": 0.1}),
        ]
    except Exception as exc:
        checks.append(("sight", "__import__", _failing(exc), {}))

    # ── Voice — 3 tools ───────────────────────────────────────────────────
    try:
        from contextpulse_voice import mcp_server as voice  # type: ignore
        checks += [
            ("voice", "get_recent_transcriptions", voice.get_recent_transcriptions, {"minutes": 1, "limit": 1}),
            ("voice", "get_voice_stats",            voice.get_voice_stats,            {"hours": 0.01}),
            ("voice", "get_vocabulary",             voice.get_vocabulary,             {"learned_only": False}),
        ]
    except Exception as exc:
        checks.append(("voice", "__import__", _failing(exc), {}))

    # ── Touch — 3 tools ───────────────────────────────────────────────────
    try:
        from contextpulse_touch import mcp_server as touch  # type: ignore
        checks += [
            ("touch", "get_recent_touch_events", touch.get_recent_touch_events, {"seconds": 1}),
            ("touch", "get_touch_stats",         touch.get_touch_stats,         {"hours": 0.01}),
            ("touch", "get_correction_history",  touch.get_correction_history,  {"limit": 1}),
        ]
    except Exception as exc:
        checks.append(("touch", "__import__", _failing(exc), {}))

    # ── Project — 4 tools (route_to_journal skipped: has write side-effects)
    try:
        from contextpulse_project import mcp_server as project  # type: ignore
        checks += [
            ("project", "identify_project",    project.identify_project,    {"text": "canary health check"}),
            ("project", "get_active_project",  project.get_active_project,  {"cwd": "", "window_title": ""}),
            ("project", "list_projects",        project.list_projects,        {}),
            ("project", "get_project_context", project.get_project_context, {"project": "ContextPulse"}),
        ]
    except Exception as exc:
        checks.append(("project", "__import__", _failing(exc), {}))

    # ── Memory — 5 tools (store → recall → search → list → forget) ────────
    try:
        from contextpulse_memory import mcp_server as memory  # type: ignore
        KEY = "__canary_healthcheck__"
        checks += [
            ("memory", "memory_store",  memory.memory_store,  {"key": KEY, "value": "canary", "tags": ["canary"], "ttl_hours": 0.01}),
            ("memory", "memory_recall", memory.memory_recall, {"key": KEY}),
            ("memory", "memory_search", memory.memory_search, {"query": "canary", "limit": 1}),
            ("memory", "memory_list",   memory.memory_list,   {"limit": 1}),
            ("memory", "memory_forget", memory.memory_forget, {"key": KEY}),
        ]
    except Exception as exc:
        checks.append(("memory", "__import__", _failing(exc), {}))

    return checks


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run_healthcheck(auto_start: bool = True, verbose: bool = False) -> HealthReport:
    report = HealthReport(timestamp=datetime.now().isoformat(timespec="seconds"))

    # Optionally start daemon
    if auto_start:
        report.daemon_started = start_daemon_if_needed()

    report.daemon_alive, report.heartbeat_age_s = check_daemon_heartbeat()

    for server, tool_name, func, kwargs in _build_checks():
        result = _call_tool(server, tool_name, func, kwargs)
        report.results.append(result)
        if verbose:
            tag = "PASS" if result.passed else "FAIL"
            print(f"  [{tag}] {server}/{tool_name}  ({result.duration_ms:.0f} ms)")
            if result.error:
                print(f"         {result.error}")

    return report


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_summary(report: HealthReport) -> None:
    bar = "=" * 62
    print(f"\n{bar}")
    print(f"ContextPulse Canary Health Check  —  {report.timestamp}")
    print(bar)

    # Daemon line
    if report.daemon_started:
        print("Daemon: STARTED by canary (was not running)")
    elif report.daemon_alive:
        print(f"Daemon: ALIVE  (heartbeat {report.heartbeat_age_s:.0f} s ago)")
    elif report.heartbeat_age_s >= 0:
        print(f"Daemon: STALE  (heartbeat {report.heartbeat_age_s:.0f} s ago)")
    else:
        print("Daemon: NOT RUNNING  (no heartbeat file)")

    # Overall
    print(f"\nTools:  {report.passed}/{report.total} healthy")

    if report.failed:
        print(f"\nFailed ({report.failed}):")
        for r in report.results:
            if not r.passed:
                print(f"  FAIL  {r.server}/{r.tool}")
                print(f"        {r.error}")

    # Per-server table
    servers: dict[str, list[ToolResult]] = {}
    for r in report.results:
        servers.setdefault(r.server, []).append(r)

    print("\nPer-server:")
    for srv, results in sorted(servers.items()):
        ok = sum(1 for r in results if r.passed)
        total = len(results)
        label = "OK      " if ok == total else "DEGRADED"
        print(f"  {srv:>10s}  {label}  {ok}/{total}")

    print(bar)
    if report.all_healthy:
        print("Result: ALL HEALTHY")
    else:
        print(f"Result: {report.failed} FAILURE(S) DETECTED")
    print()


def _report_as_dict(report: HealthReport) -> dict:
    return {
        "timestamp": report.timestamp,
        "daemon_alive": report.daemon_alive,
        "daemon_started": report.daemon_started,
        "heartbeat_age_s": report.heartbeat_age_s,
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "all_healthy": report.all_healthy,
        },
        "tools": [
            {
                "server": r.server,
                "tool": r.tool,
                "passed": r.passed,
                "duration_ms": r.duration_ms,
                "error": r.error or None,
            }
            for r in report.results
        ],
    }


def print_json(report: HealthReport) -> None:
    print(json.dumps(_report_as_dict(report), indent=2))


def save_to_log(report: HealthReport) -> None:
    """Append this run to logs/canary_results.json (keeps last 100 runs)."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    if _LOG_FILE.exists():
        try:
            runs = json.loads(_LOG_FILE.read_text(encoding="utf-8"))
            if not isinstance(runs, list):
                runs = []
        except Exception:
            runs = []

    runs.append(_report_as_dict(report))
    runs = runs[-_MAX_STORED_RUNS:]  # keep last 100

    _LOG_FILE.write_text(json.dumps(runs, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ContextPulse canary — exercises every MCP tool and reports pass/fail"
    )
    parser.add_argument("--json", action="store_true", help="Print results as JSON to stdout")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show each tool result live")
    parser.add_argument("--no-start", dest="auto_start", action="store_false",
                        help="Skip daemon auto-start")
    args = parser.parse_args()

    # Silence noisy internal loggers
    logging.basicConfig(level=logging.WARNING)
    for name in ("contextpulse", "mcp", "PIL", "faster_whisper", "httpx"):
        logging.getLogger(name).setLevel(logging.ERROR)

    if args.verbose and not args.json:
        print("Running ContextPulse canary health check...\n")

    report = run_healthcheck(auto_start=args.auto_start, verbose=args.verbose)

    # Always persist to log file
    try:
        save_to_log(report)
    except Exception as exc:
        print(f"Warning: could not write log file: {exc}", file=sys.stderr)

    if args.json:
        print_json(report)
    else:
        print_summary(report)

    sys.exit(0 if report.all_healthy else 1)


if __name__ == "__main__":
    main()
