"""macOS implementation of PlatformProvider using PyObjC.

Required packages (install via `pip install contextpulse-core[macos]`):
  - pyobjc-framework-Cocoa       (NSPasteboard, NSWorkspace, NSDistributedNotificationCenter)
  - pyobjc-framework-Quartz      (CGEvent, CGWindowListCopyWindowInfo)
  - pyobjc-framework-ApplicationServices  (AXUIElement for caret position)

All PyObjC imports are lazy (inside methods) so this module can be imported
on any platform for testing without requiring the frameworks.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from contextpulse_core.platform.base import PlatformProvider

logger = logging.getLogger(__name__)


class MacPlatformProvider(PlatformProvider):
    """macOS implementation using PyObjC / AppKit / Quartz."""

    # -- Clipboard ---------------------------------------------------------

    def get_clipboard_sequence(self) -> int:
        """Return NSPasteboard changeCount (increments on every clipboard change)."""
        try:
            from AppKit import NSPasteboard

            return NSPasteboard.generalPasteboard().changeCount()
        except Exception:
            logger.debug("get_clipboard_sequence failed", exc_info=True)
            return 0

    def get_clipboard_text(self) -> str | None:
        """Read clipboard text via NSPasteboard."""
        try:
            from AppKit import NSPasteboard, NSStringPboardType

            pb = NSPasteboard.generalPasteboard()
            text = pb.stringForType_(NSStringPboardType)
            return text if text else None
        except Exception:
            logger.debug("get_clipboard_text failed", exc_info=True)
            return None

    # -- Window info -------------------------------------------------------

    def get_foreground_window_title(self) -> str:
        """Return the focused window's title.

        Requires Screen Recording permission for window titles.
        Without it, falls back to the application name.
        """
        try:
            from AppKit import NSWorkspace
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return ""

            pid = app.processIdentifier()
            windows = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            )
            if windows:
                for w in windows:
                    if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 999) == 0:
                        title = w.get("kCGWindowName", "")
                        if title:
                            return title

            # Fallback: application name (always available)
            return app.localizedName() or ""
        except Exception:
            logger.debug("get_foreground_window_title failed", exc_info=True)
            return ""

    def get_foreground_process_name(self) -> str:
        """Return the foreground app's process/bundle name."""
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return ""

            # Prefer bundle name (e.g., "Safari" from "Safari.app")
            url = app.bundleURL()
            if url:
                bundle_name = url.lastPathComponent()
                if bundle_name and bundle_name.endswith(".app"):
                    return bundle_name[:-4]

            return app.localizedName() or ""
        except Exception:
            logger.debug("get_foreground_process_name failed", exc_info=True)
            return ""

    # -- Cursor / pointer --------------------------------------------------

    def get_cursor_pos(self) -> tuple[int, int]:
        """Return mouse cursor position via CGEvent."""
        try:
            from Quartz import CGEventCreate, CGEventGetLocation

            event = CGEventCreate(None)
            point = CGEventGetLocation(event)
            return (int(point.x), int(point.y))
        except Exception:
            logger.debug("get_cursor_pos failed", exc_info=True)
            return (0, 0)

    # -- Caret (text cursor) -----------------------------------------------

    def get_caret_position(self) -> tuple[int, int] | None:
        """Return text caret position via Accessibility API.

        Requires Accessibility permission. Returns None if permission
        is not granted or no focused text field exists.
        """
        try:
            from ApplicationServices import (
                AXIsProcessTrusted,
                AXUIElementCopyAttributeValue,
                AXUIElementCreateSystemWide,
            )
            from CoreFoundation import kCFAllocatorDefault  # noqa: F401

            if not AXIsProcessTrusted():
                return None

            system = AXUIElementCreateSystemWide()
            err, focused = AXUIElementCopyAttributeValue(
                system, "AXFocusedUIElement", None
            )
            if err != 0 or focused is None:
                return None

            err, pos_value = AXUIElementCopyAttributeValue(
                focused, "AXPosition", None
            )
            if err != 0 or pos_value is None:
                return None

            # AXPosition is an AXValue wrapping a CGPoint
            import Quartz
            from Quartz import AXValueGetValue, kAXValueTypeCGPoint

            point = Quartz.CGPoint()
            if AXValueGetValue(pos_value, kAXValueTypeCGPoint, point):
                return (int(point.x), int(point.y))

            return None
        except Exception:
            logger.debug("get_caret_position failed", exc_info=True)
            return None

    # -- Session lock detection --------------------------------------------

    def create_session_monitor(self, on_lock: callable, on_unlock: callable):
        """Return a MacSessionMonitor that listens for screen lock/unlock events."""
        return MacSessionMonitor(on_lock, on_unlock)

    # -- Single-instance guard ---------------------------------------------

    def acquire_single_instance_lock(self, name: str) -> object | None:
        """Acquire a file lock in /tmp for single-instance enforcement."""
        import fcntl

        lock_path = Path(f"/tmp/{name}.lock")
        try:
            lock_file = open(lock_path, "w")  # noqa: SIM115
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            return lock_file  # Keep reference alive — GC would close the file
        except (BlockingIOError, OSError):
            try:
                lock_file.close()
            except Exception:
                pass
            return None

    def release_single_instance_lock(self, handle: object) -> None:
        """Release the file lock."""
        import fcntl

        if handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            except Exception:
                logger.debug("release_single_instance_lock failed", exc_info=True)

    # -- Bonus: process management (matches Windows extras) ----------------

    def find_contextpulse_processes(self, exclude_pid: int | None = None) -> list[int]:
        """Find PIDs of running ContextPulse processes via pgrep."""
        pids: list[int] = []
        try:
            result = subprocess.run(
                ["pgrep", "-f", "contextpulse"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid != exclude_pid:
                        pids.append(pid)
        except Exception:
            pass
        return pids

    def kill_process(self, pid: int) -> bool:
        """Kill a process by PID. Returns True on success."""
        try:
            subprocess.run(
                ["kill", "-9", str(pid)],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False


# -- Session monitor implementation ----------------------------------------


class MacSessionMonitor:
    """Monitors screen lock/unlock via NSDistributedNotificationCenter.

    Must be started from a thread that has (or will pump) an NSRunLoop.
    The ``start()`` method creates a daemon thread that runs the observer
    and pumps CFRunLoop.
    """

    def __init__(self, on_lock: callable, on_unlock: callable) -> None:
        self._on_lock = on_lock
        self._on_unlock = on_unlock
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Begin monitoring in a background daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="session-monitor")
        self._thread.start()

    def _run(self) -> None:
        try:
            from AppKit import NSDistributedNotificationCenter
            from PyObjCTools import AppHelper

            center = NSDistributedNotificationCenter.defaultCenter()
            center.addObserverForName_object_queue_usingBlock_(
                "com.apple.screenIsLocked", None, None,
                lambda _note: self._on_lock(),
            )
            center.addObserverForName_object_queue_usingBlock_(
                "com.apple.screenIsUnlocked", None, None,
                lambda _note: self._on_unlock(),
            )
            # Pump the run loop so notifications are delivered
            AppHelper.runConsoleEventLoop()
        except Exception:
            logger.error("MacSessionMonitor failed to start", exc_info=True)
