"""ContextPulse Canary Health Check

Calls every MCP tool with minimal arguments and reports pass/fail status.
Designed for cron scheduling to catch regressions early.

Setup as a cron job (Task Scheduler on Windows):
  1. Open Task Scheduler -> Create Basic Task
  2. Trigger: Daily (or every N hours)
  3. Action: Start a program
     Program: C:\\Users\\david\\Projects\\ContextPulse\\.venv\\Scripts\\python.exe
     Arguments: C:\\Users\\david\\Projects\\ContextPulse\\scripts\\canary_healthcheck.py
     Start in: C:\\Users\\david\\Projects\\ContextPulse
  4. The script exits with code 0 if all tools pass, 1 if any fail.

Or run manually:
  python scripts/canary_healthcheck.py
  python scripts/canary_healthcheck.py --json        # JSON output
  python scripts/canary_healthcheck.py --verbose      # show return values
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure ContextPulse packages are importable when running standalone.
# The monorepo uses editable installs, but just in case:
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for pkg_dir in (_PROJECT_ROOT / "packages").iterdir():
    src = pkg_dir / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


# ---------------------------------------------------------------------------
# Result tracking
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
# Daemon heartbeat check
# ---------------------------------------------------------------------------
def check_daemon_heartbeat() -> tuple[bool, float]:
    """Check if the daemon wrote a recent heartbeat file.

    Returns (alive, age_in_seconds). alive=True if heartbeat < 60s old.
    age=-1 if no heartbeat file found.
    """
    output_dir = Path(os.environ.get(
        "CONTEXTPULSE_OUTPUT_DIR", str(Path.home() / "screenshots")
    ))
    heartbeat = output_dir / "heartbeat"
    if not heartbeat.exists():
        return False, -1.0
    try:
        ts = float(heartbeat.read_text(encoding="utf-8").strip())
        age = time.time() - ts
        return age < 60, age
    except Exception:
        return False, -1.0


# ---------------------------------------------------------------------------
# Safe tool invocation wrapper
# ---------------------------------------------------------------------------
def _call_tool(server: str, tool_name: str, func, kwargs: dict) -> ToolResult:
    """Call a tool function and return a ToolResult."""
    t0 = time.perf_counter()
    try:
        result = func(**kwargs)
        duration = (time.perf_counter() - t0) * 1000

        # Determine if the result indicates an error condition.
        # Tool functions return strings or lists; actual Python exceptions
        # are caught in the except block below.
        preview = ""
        if isinstance(result, str):
            preview = result[:200]
        elif isinstance(result, list):
            preview = f"[list of {len(result)} items]"
        elif result is None:
            preview = "None"
        else:
            preview = str(result)[:200]

        return ToolResult(
            server=server,
            tool=tool_name,
            passed=True,
            duration_ms=round(duration, 1),
            return_preview=preview,
        )
    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        return ToolResult(
            server=server,
            tool=tool_name,
            passed=False,
            duration_ms=round(duration, 1),
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Tool definitions — each entry is (server, tool_name, callable, kwargs)
#
# We import lazily inside the function so that import failures for one
# server don't block checks for other servers.
# ---------------------------------------------------------------------------
def build_tool_checks() -> list[tuple[str, str, object, dict]]:
    """Build the list of (server, tool_name, callable, kwargs) to check.

    Each tool is called with the minimal safe arguments that should succeed
    even if the daemon is not running or the database is empty.
    """
    checks = []

    # ── Sight (screen) ────────────────────────────────────────────
    try:
        from contextpulse_sight import mcp_server as sight

        # get_screenshot and get_screen_text capture the live screen,
        # which requires a display. We still test them — they should
        # return an image or a privacy-blocked message, not crash.
        checks.append(("sight", "get_screenshot", sight.get_screenshot, {"mode": "active"}))
        checks.append(("sight", "get_recent", sight.get_recent, {"count": 1, "seconds": 10}))
        checks.append(("sight", "get_screen_text", sight.get_screen_text, {}))
        checks.append(("sight", "get_buffer_status", sight.get_buffer_status, {}))
        checks.append(("sight", "get_activity_summary", sight.get_activity_summary, {"hours": 0.01}))
        checks.append(("sight", "search_history", sight.search_history, {"query": "__canary__", "minutes_ago": 1}))
        checks.append(("sight", "get_context_at", sight.get_context_at, {"minutes_ago": 0.1}))
        checks.append(("sight", "get_clipboard_history", sight.get_clipboard_history, {"count": 1}))
        checks.append(("sight", "search_clipboard", sight.search_clipboard, {"query": "__canary__", "minutes_ago": 1}))
        checks.append(("sight", "get_agent_stats", sight.get_agent_stats, {"hours": 0.01}))
        checks.append(("sight", "search_all_events", sight.search_all_events, {"query": "__canary__", "minutes_ago": 1}))
        checks.append(("sight", "get_event_timeline", sight.get_event_timeline, {"minutes_ago": 0.1}))
    except Exception as exc:
        checks.append(("sight", "__import__", lambda: None, {}))
        # We'll record the import failure as a synthetic check below
        checks[-1] = ("sight", "__import__", _make_failing_func(exc), {})

    # ── Voice ─────────────────────────────────────────────────────
    try:
        from contextpulse_voice import mcp_server as voice

        checks.append(("voice", "get_recent_transcriptions", voice.get_recent_transcriptions, {"minutes": 1, "limit": 1}))
        checks.append(("voice", "get_voice_stats", voice.get_voice_stats, {"hours": 0.01}))
        checks.append(("voice", "get_vocabulary", voice.get_vocabulary, {"learned_only": False}))
    except Exception as exc:
        checks.append(("voice", "__import__", _make_failing_func(exc), {}))

    # ── Touch ─────────────────────────────────────────────────────
    try:
        from contextpulse_touch import mcp_server as touch

        checks.append(("touch", "get_recent_touch_events", touch.get_recent_touch_events, {"seconds": 1}))
        checks.append(("touch", "get_touch_stats", touch.get_touch_stats, {"hours": 0.01}))
        checks.append(("touch", "get_correction_history", touch.get_correction_history, {"limit": 1}))
    except Exception as exc:
        checks.append(("touch", "__import__", _make_failing_func(exc), {}))

    # ── Project ───────────────────────────────────────────────────
    try:
        from contextpulse_project import mcp_server as project

        checks.append(("project", "identify_project", project.identify_project, {"text": "canary health check test"}))
        checks.append(("project", "get_active_project", project.get_active_project, {"cwd": "", "window_title": ""}))
        checks.append(("project", "list_projects", project.list_projects, {}))
        checks.append(("project", "get_project_context", project.get_project_context, {"project": "ContextPulse"}))
        # route_to_journal skipped — it has side effects (writes to journal)
    except Exception as exc:
        checks.append(("project", "__import__", _make_failing_func(exc), {}))

    # ── Memory ────────────────────────────────────────────────────
    try:
        from contextpulse_memory import mcp_server as memory

        # Use a canary key that we clean up immediately
        CANARY_KEY = "__canary_healthcheck__"
        checks.append(("memory", "memory_store", memory.memory_store, {
            "key": CANARY_KEY, "value": "canary", "tags": ["canary"], "ttl_hours": 0.01,
        }))
        checks.append(("memory", "memory_recall", memory.memory_recall, {"key": CANARY_KEY}))
        checks.append(("memory", "memory_search", memory.memory_search, {"query": "canary", "limit": 1}))
        checks.append(("memory", "memory_list", memory.memory_list, {"limit": 1}))
        checks.append(("memory", "memory_forget", memory.memory_forget, {"key": CANARY_KEY}))
    except Exception as exc:
        checks.append(("memory", "__import__", _make_failing_func(exc), {}))

    return checks


def _make_failing_func(exc: Exception):
    """Return a callable that re-raises the captured import error."""
    def _fail():
        raise ImportError(f"Server import failed: {exc}")
    return _fail


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def run_healthcheck(verbose: bool = False) -> HealthReport:
    """Execute all tool checks and return a HealthReport."""
    report = HealthReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )

    # Daemon heartbeat
    report.daemon_alive, report.heartbeat_age_s = check_daemon_heartbeat()

    # Build and run checks
    checks = build_tool_checks()
    for server, tool_name, func, kwargs in checks:
        result = _call_tool(server, tool_name, func, kwargs)
        report.results.append(result)
        if verbose:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {server}/{tool_name} ({result.duration_ms:.0f}ms)")
            if result.error:
                print(f"         Error: {result.error}")

    return report


def print_summary(report: HealthReport):
    """Print a human-readable summary."""
    print(f"\n{'='*60}")
    print(f"ContextPulse Canary Health Check — {report.timestamp}")
    print(f"{'='*60}")

    # Daemon status
    if report.daemon_alive:
        print(f"Daemon: ALIVE (heartbeat {report.heartbeat_age_s:.0f}s ago)")
    elif report.heartbeat_age_s >= 0:
        print(f"Daemon: STALE (heartbeat {report.heartbeat_age_s:.0f}s ago)")
    else:
        print("Daemon: NO HEARTBEAT (not running or no heartbeat file)")

    # Tool summary
    print(f"\nTools: {report.passed}/{report.total} healthy")

    if report.failed > 0:
        print(f"\nFailed tools ({report.failed}):")
        for r in report.results:
            if not r.passed:
                print(f"  FAIL  {r.server}/{r.tool} — {r.error}")

    # Per-server breakdown
    servers = {}
    for r in report.results:
        servers.setdefault(r.server, []).append(r)

    print(f"\nPer-server breakdown:")
    for server, results in sorted(servers.items()):
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        status = "OK" if passed == total else "DEGRADED"
        print(f"  {server:>10s}: {passed}/{total} ({status})")

    print(f"{'='*60}")
    if report.all_healthy:
        print("Result: ALL HEALTHY")
    else:
        print(f"Result: {report.failed} FAILURE(S) DETECTED")
    print()


def print_json(report: HealthReport):
    """Print results as JSON for machine consumption."""
    data = {
        "timestamp": report.timestamp,
        "daemon_alive": report.daemon_alive,
        "heartbeat_age_s": round(report.heartbeat_age_s, 1),
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
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="ContextPulse canary health check")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show each tool result as it runs")
    args = parser.parse_args()

    # Suppress noisy loggers during health check
    logging.basicConfig(level=logging.WARNING)
    for name in ("contextpulse", "mcp", "PIL", "faster_whisper"):
        logging.getLogger(name).setLevel(logging.ERROR)

    if args.verbose:
        print("Running ContextPulse canary health check...\n")

    report = run_healthcheck(verbose=args.verbose)

    if args.json:
        print_json(report)
    else:
        print_summary(report)

    # Exit code: 0 = all healthy, 1 = failures detected
    sys.exit(0 if report.all_healthy else 1)


if __name__ == "__main__":
    main()
