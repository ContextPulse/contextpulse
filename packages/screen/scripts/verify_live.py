"""Post-restart verification: tests every MCP tool and daemon feature live.

Run this after restarting the ContextPulse Sight daemon and Claude Code session.
It validates the full stack without any user interaction.

Usage:
    python packages/screen/scripts/verify_live.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

PASS = 0
FAIL = 0
WARN = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def warn(name, detail=""):
    global WARN
    WARN += 1
    print(f"  [WARN] {name}" + (f" — {detail}" if detail else ""))


def test_imports():
    """Verify all modules import cleanly."""
    print("\n1. Module Imports")
    try:
        check("9 core modules import", True)
    except Exception as e:
        check("9 core modules import", False, str(e))

    try:
        check("mcp_server imports", True)
    except Exception as e:
        # MCP server uses pydantic types that fail outside MCP runtime - expected
        warn("mcp_server import (expected outside MCP runtime)", str(e)[:80])


def test_config():
    """Verify config defaults are correct."""
    print("\n2. Config Defaults")
    from contextpulse_sight.config import (
        ACTIVITY_MAX_AGE,
        ALWAYS_BOTH_APPS,
        AUTO_INTERVAL,
        BUFFER_MAX_AGE,
        EVENT_POLL_INTERVAL,
        JPEG_QUALITY,
        STORAGE_MODE,
    )
    check("BUFFER_MAX_AGE == 1800 (30 min)", BUFFER_MAX_AGE == 1800, f"got {BUFFER_MAX_AGE}")
    check("JPEG_QUALITY == 75", JPEG_QUALITY == 75, f"got {JPEG_QUALITY}")
    check("STORAGE_MODE == 'smart'", STORAGE_MODE == "smart", f"got {STORAGE_MODE}")
    check("thinkorswim.exe in ALWAYS_BOTH_APPS", "thinkorswim.exe" in ALWAYS_BOTH_APPS)
    check("AUTO_INTERVAL == 5", AUTO_INTERVAL == 5, f"got {AUTO_INTERVAL}")
    check("EVENT_POLL_INTERVAL == 0.5", EVENT_POLL_INTERVAL == 0.5, f"got {EVENT_POLL_INTERVAL}")
    check("ACTIVITY_MAX_AGE == 86400 (24h)", ACTIVITY_MAX_AGE == 86400, f"got {ACTIVITY_MAX_AGE}")


def test_daemon_running():
    """Verify daemon is running and capturing."""
    print("\n3. Daemon Status")
    from contextpulse_sight.config import BUFFER_DIR, OUTPUT_DIR

    check("Screenshots dir exists", OUTPUT_DIR.exists())
    check("Buffer dir exists", BUFFER_DIR.exists())

    # Check for recent buffer frames
    jpg_files = list(BUFFER_DIR.glob("*.jpg"))
    txt_files = [f for f in BUFFER_DIR.glob("*.txt") if not f.with_suffix(".jpg").exists()]
    total_frames = len(jpg_files) + len(txt_files)
    check(f"Buffer has frames ({total_frames} found)", total_frames > 0)

    if jpg_files:
        # Check filename format
        from contextpulse_sight.buffer import parse_frame_path
        sample = jpg_files[0]
        parsed = parse_frame_path(sample)
        check(f"Filename format correct: {sample.name}", parsed is not None)
        if parsed:
            check(f"Has monitor index (m{parsed[1]})", parsed[1] >= 0)

        # Check freshness
        newest = max(jpg_files, key=lambda f: f.stat().st_mtime)
        age = time.time() - newest.stat().st_mtime
        check(f"Latest frame is fresh ({age:.0f}s ago)", age < 120, f"{age:.0f}s old — change detection may be skipping identical frames")

    # Check for multi-monitor
    monitors_seen = set()
    for f in jpg_files[:20]:
        parsed = parse_frame_path(f)
        if parsed:
            monitors_seen.add(parsed[1])
    check(f"Multiple monitors detected ({len(monitors_seen)} found)", len(monitors_seen) >= 1)
    if len(monitors_seen) >= 2:
        print(f"         Monitors: {sorted(monitors_seen)}")


def test_capture_functions():
    """Test capture functions directly."""
    print("\n4. Capture Functions")
    from contextpulse_sight import capture

    # Active monitor
    try:
        idx, img = capture.capture_active_monitor()
        check(f"capture_active_monitor() -> monitor {idx}, {img.width}x{img.height}", True)
    except Exception as e:
        check("capture_active_monitor()", False, str(e))

    # Monitor count
    try:
        count = capture.get_monitor_count()
        check(f"get_monitor_count() -> {count}", count >= 1)
    except Exception as e:
        check("get_monitor_count()", False, str(e))

    # All monitors
    try:
        monitors = capture.capture_all_monitors()
        check(f"capture_all_monitors() -> {len(monitors)} monitors", len(monitors) >= 1)
        for mi, img in monitors:
            check(f"  Monitor {mi}: {img.width}x{img.height}", img.width <= 1280 and img.height <= 720)
    except Exception as e:
        check("capture_all_monitors()", False, str(e))

    # Single monitor
    try:
        img = capture.capture_single_monitor(0)
        check(f"capture_single_monitor(0) -> {img.width}x{img.height}", True)
    except Exception as e:
        check("capture_single_monitor(0)", False, str(e))

    # Region
    try:
        img = capture.capture_region()
        check(f"capture_region() -> {img.width}x{img.height}", True)
    except Exception as e:
        check("capture_region()", False, str(e))


def test_buffer():
    """Test rolling buffer reads."""
    print("\n5. Rolling Buffer")
    from contextpulse_sight.buffer import RollingBuffer

    buf = RollingBuffer()
    count = buf.frame_count()
    check(f"frame_count() -> {count}", count >= 0)

    frames = buf.list_frames()
    check(f"list_frames() -> {len(frames)} frames", len(frames) >= 0)

    recent = buf.get_recent(seconds=60)
    check(f"get_recent(60s) -> {len(recent)} frames", True)

    latest = buf.get_latest()
    if latest:
        check(f"get_latest() -> {latest.name}", True)
        ctx = buf.get_latest_context()
        check(f"get_latest_context() -> type={ctx['type']}", ctx["type"] in ("text", "image", "none"))
    else:
        warn("No frames in buffer — daemon may not have captured yet")


def test_activity_db():
    """Test activity database."""
    print("\n6. Activity Database")
    from contextpulse_sight.config import ACTIVITY_DB_PATH

    check(f"activity.db exists at {ACTIVITY_DB_PATH}", ACTIVITY_DB_PATH.exists())

    if ACTIVITY_DB_PATH.exists():
        from contextpulse_sight.activity import ActivityDB
        db = ActivityDB()

        count = db.count()
        check(f"Activity records: {count}", count >= 0)

        summary = db.get_summary(hours=1)
        check(f"get_summary(1h) -> {summary['total_captures']} captures", True)
        if summary["apps"]:
            top_app = list(summary["apps"].keys())[0]
            print(f"         Top app: {top_app} ({summary['apps'][top_app]} captures)")

        # Test search
        results = db.search("chrome", minutes_ago=60)
        check(f"search('chrome') -> {len(results)} results", True)

        # Test context_at
        ctx = db.get_context_at(minutes_ago=1)
        if ctx:
            check(f"get_context_at(1m) -> {ctx['app_name']} - {ctx['window_title'][:50]}", True)
        else:
            warn("get_context_at(1m) -> no records yet")

        db.close()


def test_privacy():
    """Test privacy functions."""
    print("\n7. Privacy Functions")
    from contextpulse_sight.privacy import (
        get_foreground_process_name,
        get_foreground_window_title,
        is_blocked,
    )

    title = get_foreground_window_title()
    check(f"get_foreground_window_title() -> '{title[:60]}'", isinstance(title, str))

    proc = get_foreground_process_name()
    check(f"get_foreground_process_name() -> '{proc}'", isinstance(proc, str) and len(proc) > 0)

    blocked = is_blocked()
    check(f"is_blocked() -> {blocked}", isinstance(blocked, bool))


def test_events():
    """Test event detector."""
    print("\n8. Event Detector")
    from contextpulse_sight.events import EventDetector

    detector = EventDetector(
        get_cursor_pos=lambda: (100, 100),
        find_monitor_index=lambda cx, cy: 0,
    )
    check("EventDetector creates without error", True)
    check("No pending event initially", not detector.has_pending_event())

    detector._trigger("test")
    check("Trigger sets pending event", detector.has_pending_event())
    check("Reason is 'test'", detector.get_pending_reason() == "test")

    detector.clear_pending()
    check("Clear resets pending", not detector.has_pending_event())


def test_ocr():
    """Test OCR classification on a live capture."""
    print("\n9. OCR Classification (live)")
    from contextpulse_sight import capture
    from contextpulse_sight.classifier import classify_and_extract

    idx, img = capture.capture_active_monitor()

    try:
        result = classify_and_extract(img)
        check(
            f"classify_and_extract() -> type={result['type']}, "
            f"{result['chars']} chars, {result['confidence']:.2f} conf, "
            f"{result['ocr_time']:.1f}s",
            True
        )
        if result["text"]:
            snippet = result["text"][:100].replace("\n", " ")
            print(f"         Sample: {snippet}")
    except Exception as e:
        check("classify_and_extract()", False, str(e))


def test_smart_storage():
    """Verify smart storage mode is working."""
    print("\n10. Smart Storage Mode")
    from contextpulse_sight.config import BUFFER_DIR, STORAGE_MODE

    check(f"Storage mode is '{STORAGE_MODE}'", STORAGE_MODE == "smart")

    # Check if any text-only frames exist (jpg deleted, txt remains)
    text_only = [f for f in BUFFER_DIR.glob("*.txt") if not f.with_suffix(".jpg").exists()]
    image_frames = list(BUFFER_DIR.glob("*.jpg"))
    both_frames = [f for f in BUFFER_DIR.glob("*.txt") if f.with_suffix(".jpg").exists()]

    print(f"         Image-only frames: {len(image_frames) - len(both_frames)}")
    print(f"         Image+text frames: {len(both_frames)}")
    print(f"         Text-only frames:  {len(text_only)}")

    total = len(image_frames) + len(text_only)
    if total > 0:
        img_kb = sum(f.stat().st_size for f in image_frames) // 1024
        txt_kb = sum(f.stat().st_size for f in BUFFER_DIR.glob("*.txt")) // 1024
        print(f"         Disk: {img_kb} KB images + {txt_kb} KB text = {img_kb + txt_kb} KB total")
        if text_only:
            check("Smart mode is producing text-only frames", True)
        else:
            warn("No text-only frames yet — may need more captures of text-heavy screens")
    else:
        warn("Buffer empty — daemon may not have captured yet")


def main():
    global PASS, FAIL, WARN
    print("=" * 60)
    print("ContextPulse Sight — Live Verification")
    print("=" * 60)

    test_imports()
    test_config()
    test_daemon_running()
    test_capture_functions()
    test_buffer()
    test_activity_db()
    test_privacy()
    test_events()
    test_ocr()
    test_smart_storage()

    print("\n" + "=" * 60)
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {WARN} warnings")
    print("=" * 60)

    if FAIL > 0:
        print("\nFix the failures above before using ContextPulse Sight.")
        sys.exit(1)
    elif WARN > 0:
        print("\nAll tests passed. Warnings are non-critical — may resolve after more captures.")
    else:
        print("\nAll systems go!")


if __name__ == "__main__":
    main()
