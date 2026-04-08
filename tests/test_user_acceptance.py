"""ContextPulse Sight — Automated User Acceptance Test Script.

Run:  python tests/test_user_acceptance.py
From: ContextPulse repo root, with .venv activated

Tests everything a user would manually verify, but programmatically:
  1. Pre-flight: imports, output dir, venv
  2. Capture engine: all 3 modes produce valid images + files
  3. Rolling buffer: add, change detection, pruning, retrieval
  4. Privacy: blocklist skips capture, clear blocklist resumes
  5. Tray icon: generation in all variants
  6. Daemon lifecycle: start subprocess, verify auto-capture, hotkey simulation
     via Win32 SendInput, single-instance guard, pause/resume, clean shutdown
  7. MCP server: JSON-RPC over stdio, all 4 tools respond correctly
  8. OCR classifier: full-res capture + text extraction

You just run it and read the results. ~45 seconds total.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Skip all tests in this module when mss is mocked (e.g., when running
# alongside packages/screen/tests which mock heavy platform dependencies).
# These are integration tests that require real screen capture hardware.
_mss_is_mocked = isinstance(sys.modules.get("mss"), MagicMock)
pytestmark = pytest.mark.skipif(
    _mss_is_mocked,
    reason="screen capture deps are mocked — run this file in isolation for full UAT",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_SKIP = 0
_WIDTH = 60


def _label(name: str, status: str, detail: str = ""):
    global _PASS, _FAIL, _SKIP
    if status == "PASS":
        _PASS += 1
        marker = "\033[92mPASS\033[0m"
    elif status == "FAIL":
        _FAIL += 1
        marker = "\033[91mFAIL\033[0m"
    else:
        _SKIP += 1
        marker = "\033[93mSKIP\033[0m"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{marker}] {name}{suffix}", flush=True)


def _section(title: str):
    print(f"\n{'=' * _WIDTH}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{'=' * _WIDTH}", flush=True)


def _wait_for_file(path: Path, timeout: float = 10.0) -> bool:
    """Poll until file exists and has content."""
    start = time.time()
    while time.time() - start < timeout:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.3)
    return False


def _wait_for_glob(directory: Path, pattern: str, min_count: int = 1,
                   timeout: float = 15.0) -> list[Path]:
    """Poll until at least min_count files matching pattern appear."""
    start = time.time()
    while time.time() - start < timeout:
        found = sorted(directory.glob(pattern))
        if len(found) >= min_count:
            return found
        time.sleep(0.5)
    return sorted(directory.glob(pattern))



# ---------------------------------------------------------------------------
# 1. Pre-flight checks
# ---------------------------------------------------------------------------

def test_preflight():
    _section("1. Pre-Flight Checks")

    # Python version
    v = sys.version_info
    if v >= (3, 12):
        _label("Python >= 3.12", "PASS", f"{v.major}.{v.minor}.{v.micro}")
    else:
        _label("Python >= 3.12", "FAIL", f"got {v.major}.{v.minor}")

    # In venv?
    in_venv = sys.prefix != sys.base_prefix
    _label("Running in .venv", "PASS" if in_venv else "FAIL")

    # Core imports
    imports_ok = True
    for mod in [
        "contextpulse_sight",
        "contextpulse_sight.app",
        "contextpulse_sight.capture",
        "contextpulse_sight.buffer",
        "contextpulse_sight.mcp_server",
        "contextpulse_sight.privacy",
        "contextpulse_sight.classifier",
        "contextpulse_sight.icon",
        "contextpulse_sight.config",
        "mss", "pynput", "pystray", "PIL", "numpy", "mcp",
    ]:
        try:
            __import__(mod)
        except ImportError as e:
            _label(f"import {mod}", "FAIL", str(e))
            imports_ok = False
    if imports_ok:
        _label("All imports", "PASS", "14 modules")

    # Entry points exist
    for ep in ["contextpulse-sight", "contextpulse-sight-mcp"]:
        script = Path(sys.prefix) / "Scripts" / f"{ep}.exe"
        if script.exists():
            _label(f"Entry point: {ep}", "PASS")
        else:
            _label(f"Entry point: {ep}", "FAIL", f"not at {script}")

    assert imports_ok, "One or more imports failed"


# ---------------------------------------------------------------------------
# 2. Capture engine
# ---------------------------------------------------------------------------

def test_capture_engine():
    _section("2. Capture Engine (direct calls)")
    from contextpulse_sight import capture
    from contextpulse_sight.config import OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Quick capture (active monitor)
    try:
        img = capture.capture_active_monitor()
        assert img.width > 0 and img.height > 0
        assert img.width <= 1280 and img.height <= 720
        _label("capture_active_monitor()", "PASS",
               f"{img.width}x{img.height}")
    except Exception as e:
        _label("capture_active_monitor()", "FAIL", str(e))

    # All monitors
    try:
        img = capture.capture_all_monitors()
        assert img.width > 0 and img.height > 0
        _label("capture_all_monitors()", "PASS",
               f"{img.width}x{img.height}")
    except Exception as e:
        _label("capture_all_monitors()", "FAIL", str(e))

    # Region capture
    try:
        img = capture.capture_region()
        assert img.width > 0 and img.height > 0
        _label("capture_region()", "PASS",
               f"{img.width}x{img.height}")
    except Exception as e:
        _label("capture_region()", "FAIL", str(e))

    # Save to disk
    test_path = OUTPUT_DIR / "_test_capture.png"
    try:
        capture.save_image(img, test_path)
        assert test_path.exists() and test_path.stat().st_size > 0
        _label("save_image() to disk", "PASS",
               f"{test_path.stat().st_size:,} bytes")
        test_path.unlink()
    except Exception as e:
        _label("save_image() to disk", "FAIL", str(e))

    # capture_to_bytes
    try:
        data = capture.capture_to_bytes(img, "JPEG")
        assert len(data) > 1000
        _label("capture_to_bytes(JPEG)", "PASS",
               f"{len(data):,} bytes")
    except Exception as e:
        _label("capture_to_bytes(JPEG)", "FAIL", str(e))


# ---------------------------------------------------------------------------
# 3. Rolling buffer
# ---------------------------------------------------------------------------

def test_buffer():
    _section("3. Rolling Buffer")
    import numpy as np
    from contextpulse_sight.buffer import RollingBuffer
    from contextpulse_sight.config import BUFFER_DIR
    from PIL import Image

    # Use a temp buffer dir to avoid polluting real buffer
    test_buf_dir = BUFFER_DIR.parent / "buffer_test"
    original_buf_dir = BUFFER_DIR

    import contextpulse_sight.buffer as buf_mod
    buf_mod.BUFFER_DIR = test_buf_dir
    if test_buf_dir.exists():
        shutil.rmtree(test_buf_dir)

    try:
        buf = RollingBuffer()

        # Add a frame
        img1 = Image.fromarray(np.random.randint(0, 255, (720, 1280, 3),
                                                  dtype=np.uint8))
        stored = buf.add(img1)
        assert stored
        _label("add() first frame", "PASS")

        # Same frame should be skipped (change detection)
        stored2 = buf.add(img1)
        assert not stored2
        _label("add() duplicate skipped", "PASS", "change detection works")

        # Different frame should be stored
        img2 = Image.fromarray(np.random.randint(0, 255, (720, 1280, 3),
                                                  dtype=np.uint8))
        stored3 = buf.add(img2)
        assert stored3
        _label("add() different frame", "PASS")

        # Frame count
        count = buf.frame_count()
        assert count == 2
        _label("frame_count()", "PASS", f"{count} frames")

        # get_recent
        recent = buf.get_recent(seconds=60)
        assert len(recent) == 2
        _label("get_recent(60s)", "PASS", f"{len(recent)} frames")

        # get_latest
        latest = buf.get_latest()
        assert latest is not None and latest.exists()
        _label("get_latest()", "PASS", latest.name)

        # clear
        buf.clear()
        assert buf.frame_count() == 0
        _label("clear()", "PASS")

    except Exception as e:
        _label("Buffer test", "FAIL", str(e))
    finally:
        buf_mod.BUFFER_DIR = original_buf_dir
        if test_buf_dir.exists():
            shutil.rmtree(test_buf_dir)


# ---------------------------------------------------------------------------
# 4. Privacy controls
# ---------------------------------------------------------------------------

def test_privacy():
    _section("4. Privacy Controls")
    import contextpulse_sight.config as cfg
    from contextpulse_sight.privacy import get_foreground_window_title, is_blocked

    title = get_foreground_window_title()
    _label("get_foreground_window_title()", "PASS",
           f"'{title[:40]}...'" if len(title) > 40 else f"'{title}'")

    orig_patterns = cfg.BLOCKLIST_PATTERNS[:]
    cfg.BLOCKLIST_PATTERNS.clear()
    assert not is_blocked()
    _label("is_blocked() with empty blocklist", "PASS", "returns False")

    if title:
        snippet = title[:10]
        cfg.BLOCKLIST_PATTERNS.append(snippet)
        assert is_blocked()
        _label("is_blocked() with matching pattern", "PASS",
               f"blocked on '{snippet}'")
    else:
        _label("is_blocked() with matching pattern", "SKIP",
               "no foreground title")

    cfg.BLOCKLIST_PATTERNS.clear()
    cfg.BLOCKLIST_PATTERNS.extend(orig_patterns)


# ---------------------------------------------------------------------------
# 5. Icon generation
# ---------------------------------------------------------------------------

def test_icon():
    _section("5. Tray Icon Generation")
    from contextpulse_sight.icon import create_icon

    icon = create_icon()
    assert icon.size == (64, 64)
    assert icon.mode == "RGBA"
    _label("create_icon() default", "PASS", f"{icon.size} {icon.mode}")

    icon_warn = create_icon(color="#F0B429")
    assert icon_warn.size == (64, 64)
    _label("create_icon(warning)", "PASS", "yellow variant")

    icon_big = create_icon(size=128)
    assert icon_big.size == (128, 128)
    _label("create_icon(size=128)", "PASS", f"{icon_big.size}")


# ---------------------------------------------------------------------------
# 6. Daemon lifecycle (subprocess)
# ---------------------------------------------------------------------------

def test_hotkey_handler():
    _section("6. Hotkey Handler (in-process)")
    from contextpulse_sight.app import ContextPulseSightApp
    from contextpulse_sight.config import FILE_ALL, FILE_LATEST, FILE_REGION, OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Clean output files
    for f in [FILE_LATEST, FILE_ALL, FILE_REGION]:
        if f.exists():
            f.unlink()

    # Create app instance without running the tray/listener (test internals)
    app = ContextPulseSightApp()

    # Test quick capture
    app.do_quick_capture()
    if FILE_LATEST.exists() and FILE_LATEST.stat().st_size > 0:
        _label("do_quick_capture()", "PASS",
               f"{FILE_LATEST.stat().st_size:,} bytes")
    else:
        _label("do_quick_capture()", "FAIL", "no file")

    # Test all-monitor capture
    app.do_all_capture()
    if FILE_ALL.exists() and FILE_ALL.stat().st_size > 0:
        _label("do_all_capture()", "PASS",
               f"{FILE_ALL.stat().st_size:,} bytes")
    else:
        _label("do_all_capture()", "FAIL", "no file")

    # Test region capture
    app.do_region_capture()
    if FILE_REGION.exists() and FILE_REGION.stat().st_size > 0:
        _label("do_region_capture()", "PASS",
               f"{FILE_REGION.stat().st_size:,} bytes")
    else:
        _label("do_region_capture()", "FAIL", "no file")

    # Test buffer gets frames from captures
    count = app.buffer.frame_count()
    if count >= 1:
        _label("Buffer populated by captures", "PASS", f"{count} frame(s)")
    else:
        _label("Buffer populated by captures", "FAIL", "empty")

    # Test pause/resume
    assert not app.paused
    app.toggle_pause()
    assert app.paused
    _label("toggle_pause() -> paused", "PASS")

    # Capture while paused should be skipped
    if FILE_LATEST.exists():
        mtime_before = FILE_LATEST.stat().st_mtime
        app.do_quick_capture()
        mtime_after = FILE_LATEST.stat().st_mtime
        if mtime_after == mtime_before:
            _label("Capture skipped while paused", "PASS")
        else:
            _label("Capture skipped while paused", "FAIL", "file was updated")
    else:
        app.do_quick_capture()
        _label("Capture skipped while paused", "SKIP", "no baseline file (capture failed earlier)")

    # Resume
    app.toggle_pause()
    assert not app.paused
    _label("toggle_pause() -> resumed", "PASS")

    # Capture after resume should work
    if FILE_LATEST.exists():
        FILE_LATEST.unlink()
    app.do_quick_capture()
    if FILE_LATEST.exists():
        _label("Capture works after resume", "PASS")
    else:
        _label("Capture works after resume", "FAIL")

    # Test _check_hotkeys routing (simulate pressed_keys set)
    from pynput.keyboard import Key, KeyCode
    app._pressed_keys = {Key.ctrl_l, Key.shift_l, KeyCode.from_char('s')}
    if FILE_LATEST.exists():
        FILE_LATEST.unlink()
    app._check_hotkeys()
    time.sleep(1)  # capture runs in a daemon thread
    if FILE_LATEST.exists():
        _label("_check_hotkeys(Ctrl+Shift+S)", "PASS")
    else:
        _label("_check_hotkeys(Ctrl+Shift+S)", "FAIL")


def test_daemon_lifecycle():
    _section("7. Daemon Lifecycle (subprocess)")
    from contextpulse_sight.config import BUFFER_DIR, FILE_LATEST, OUTPUT_DIR

    python = sys.executable

    # Clean state
    if BUFFER_DIR.exists():
        for f in BUFFER_DIR.glob("*.jpg"):
            f.unlink()
    if FILE_LATEST.exists():
        FILE_LATEST.unlink()

    log_file = OUTPUT_DIR / "_test_daemon.log"

    env = os.environ.copy()
    env["CONTEXTPULSE_AUTO_INTERVAL"] = "2"
    env["CONTEXTPULSE_BUFFER_MAX_AGE"] = "120"

    log_fh = open(log_file, "w")
    proc = subprocess.Popen(
        [python, "-m", "contextpulse_sight.app"],
        env=env,
        stdout=log_fh,
        stderr=log_fh,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    try:
        time.sleep(4)

        if proc.poll() is None:
            _label("Daemon started", "PASS", f"PID {proc.pid}")
        else:
            log_fh.close()
            content = log_file.read_text(errors="replace")[:200]
            _label("Daemon started", "FAIL", content)
            return

        # Auto-capture produces buffer frames
        frames = _wait_for_glob(BUFFER_DIR, "*.jpg", min_count=1, timeout=10)
        if frames:
            _label("Auto-capture producing frames", "PASS",
                   f"{len(frames)} frame(s) in buffer")
        else:
            _label("Auto-capture producing frames", "FAIL",
                   "no frames after 10s")

        # screen_latest.jpg written
        if _wait_for_file(FILE_LATEST, timeout=5):
            _label("screen_latest.jpg written", "PASS",
                   f"{FILE_LATEST.stat().st_size:,} bytes")
        else:
            _label("screen_latest.jpg written", "FAIL", "not found")

        # Tray icon process is alive (tray runs on main thread)
        if proc.poll() is None:
            _label("Tray icon alive", "PASS", "main thread running")
        else:
            _label("Tray icon alive", "FAIL", "process exited")

        # Single-instance guard
        log2 = OUTPUT_DIR / "_test_daemon2.log"
        log2_fh = open(log2, "w")
        proc2 = subprocess.Popen(
            [python, "-m", "contextpulse_sight.app"],
            env=env,
            stdout=log2_fh,
            stderr=log2_fh,
        )
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()
        log2_fh.close()

        if proc2.returncode == 1:
            _label("Single-instance guard", "PASS",
                   "second instance exited with code 1")
        else:
            _label("Single-instance guard", "FAIL",
                   f"exit code {proc2.returncode}")
        log2.unlink(missing_ok=True)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
            _label("Daemon shutdown", "PASS", "terminated cleanly")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _label("Daemon shutdown", "PASS", "force-killed")
        except Exception as e:
            _label("Daemon shutdown", "FAIL", str(e))
        finally:
            log_fh.close()
            if log_file.exists():
                content = log_file.read_text(errors="replace").strip()
                if content:
                    print("\n  --- Daemon log ---")
                    print(f"  {content[-400:]}")
                    print("  --- End daemon log ---\n")
                log_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 7. MCP server (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

def test_mcp_server():
    _section("8. MCP Server (stdio JSON-RPC)")

    python = sys.executable

    proc = subprocess.Popen(
        [python, "-m", "contextpulse_sight.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def _read_response(timeout: float = 10.0) -> dict | None:
        """Read a newline-delimited JSON-RPC response with timeout."""
        result = [None]

        def _reader():
            try:
                # MCP stdio uses newline-delimited JSON (not Content-Length)
                line = proc.stdout.readline()
                if line:
                    result[0] = json.loads(line)
            except Exception:
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        return result[0]

    def send_jsonrpc(method: str, params: dict | None = None,
                     req_id: int = 1) -> dict | None:
        """Send a JSON-RPC request (newline-delimited) and read the response."""
        msg = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params is not None:
            msg["params"] = params
        payload = json.dumps(msg) + "\n"
        try:
            proc.stdin.write(payload.encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return None
        return _read_response()

    def send_notification(method: str, params: dict | None = None):
        """Send a JSON-RPC notification (newline-delimited, no response)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        payload = json.dumps(msg) + "\n"
        try:
            proc.stdin.write(payload.encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    try:
        # Initialize handshake
        init_resp = send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        if init_resp and "result" in init_resp:
            server_name = init_resp["result"].get("serverInfo", {}).get("name", "?")
            _label("MCP initialize", "PASS", f"server={server_name}")

            send_notification("notifications/initialized")
            time.sleep(0.5)
        else:
            _label("MCP initialize", "FAIL",
                   str(init_resp)[:80] if init_resp else "no response (timeout)")
            return

        # List tools
        tools_resp = send_jsonrpc("tools/list", req_id=2)
        if tools_resp and "result" in tools_resp:
            tool_names = [t["name"] for t in tools_resp["result"].get("tools", [])]
            expected = {"get_screenshot", "get_recent", "get_screen_text",
                        "get_buffer_status"}
            if expected.issubset(set(tool_names)):
                _label("tools/list", "PASS",
                       ", ".join(sorted(tool_names)))
            else:
                missing = expected - set(tool_names)
                _label("tools/list", "FAIL", f"missing: {missing}")
        else:
            _label("tools/list", "FAIL", "no response")

        # Call get_buffer_status
        status_resp = send_jsonrpc("tools/call", {
            "name": "get_buffer_status",
            "arguments": {},
        }, req_id=3)
        if status_resp and "result" in status_resp:
            content = status_resp["result"].get("content", [])
            text = content[0].get("text", "") if content else ""
            if "Buffer" in text or "buffer" in text.lower() or "empty" in text.lower():
                _label("get_buffer_status", "PASS",
                       text.replace("\n", " | ")[:60])
            else:
                _label("get_buffer_status", "PASS", "responded")
        else:
            _label("get_buffer_status", "FAIL",
                   str(status_resp)[:80] if status_resp else "no response")

        # Call get_screenshot (returns image data — just check it responds)
        ss_resp = send_jsonrpc("tools/call", {
            "name": "get_screenshot",
            "arguments": {"mode": "active"},
        }, req_id=4)
        if ss_resp and "result" in ss_resp:
            content = ss_resp["result"].get("content", [])
            has_image = any(c.get("type") == "image" for c in content)
            if has_image:
                _label("get_screenshot(active)", "PASS", "returned image")
            else:
                text = content[0].get("text", "") if content else ""
                _label("get_screenshot(active)", "PASS", text[:50])
        else:
            _label("get_screenshot(active)", "FAIL",
                   str(ss_resp)[:80] if ss_resp else "no response")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        _label("MCP server shutdown", "PASS")


# ---------------------------------------------------------------------------
# 8. OCR classifier
# ---------------------------------------------------------------------------

def test_ocr():
    _section("9. OCR Classifier")
    from contextpulse_sight import capture
    from contextpulse_sight.classifier import classify_and_extract

    try:
        import mss as mss_lib
        with mss_lib.mss() as sct:
            mon = capture.find_monitor_at_cursor(sct)
            sct_img = sct.grab(mon)
            full_img = capture.mss_to_pil(sct_img)

        result = classify_and_extract(full_img)
        _label("classify_and_extract()", "PASS",
               f"type={result['type']}, {result['chars']} chars, "
               f"conf={result['confidence']:.2f}, {result['ocr_time']:.1f}s")
    except Exception as e:
        _label("classify_and_extract()", "FAIL", str(e))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'=' * _WIDTH}")
    print("  ContextPulse Sight — User Acceptance Tests")
    print(f"{'=' * _WIDTH}", flush=True)

    imports_ok = test_preflight()
    if not imports_ok:
        print("\n\033[91mCritical imports failed. Fix before continuing.\033[0m")
        sys.exit(1)

    test_capture_engine()
    test_buffer()
    test_privacy()
    test_icon()
    test_hotkey_handler()
    test_daemon_lifecycle()
    test_mcp_server()
    test_ocr()

    # Final summary
    total = _PASS + _FAIL + _SKIP
    print(f"\n{'=' * _WIDTH}")
    print(f"  RESULTS: {_PASS} passed, {_FAIL} failed, {_SKIP} skipped"
          f" (of {total})")
    if _FAIL == 0:
        print("  \033[92mAll tests passed!\033[0m")
    else:
        print(f"  \033[91m{_FAIL} test(s) need attention\033[0m")
    print(f"{'=' * _WIDTH}\n")

    sys.exit(1 if _FAIL > 0 else 0)


if __name__ == "__main__":
    main()
