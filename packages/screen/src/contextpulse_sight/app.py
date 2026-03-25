"""Main application: system tray + global hotkeys + auto-capture with rolling buffer."""

import ctypes
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from pynput import keyboard

from contextpulse_sight import capture
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.config import (
    AUTO_INTERVAL, BUFFER_MAX_AGE, CHANGE_THRESHOLD,
    FILE_LATEST, FILE_REGION, OUTPUT_DIR,
)
from contextpulse_sight.events import EventDetector
from contextpulse_sight.icon import _COLORS, create_icon
from contextpulse_sight.clipboard import ClipboardMonitor
from contextpulse_sight.ocr_worker import OCRWorker
from contextpulse_sight.privacy import (
    SessionMonitor, get_foreground_process_name, get_foreground_window_title, is_blocked,
)
from contextpulse_sight.sight_module import SightModule

# Core productization imports (settings, first-run, licensing)
from contextpulse_core.first_run import is_first_run, show_welcome_dialog
from contextpulse_core.settings import show_settings
from contextpulse_core.license import is_licensed, has_memory_access
from contextpulse_core.license_dialog import show_nag_dialog
from contextpulse_core.spine import EventBus

_WARNING_COLOR = _COLORS.get("dark", {}).get("warning", "#F0B429")

_LOG_FILE = OUTPUT_DIR / "contextpulse_sight.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("contextpulse.sight")


class ContextPulseSightApp:
    def __init__(self):
        self.paused = False
        self._user_paused = False  # tracks manual pause vs auto-pause from lock
        self.stop_event = threading.Event()
        self._pressed_keys: set = set()
        self.buffer = RollingBuffer()
        self.activity_db = ActivityDB()
        self._event_detector = EventDetector()
        self._ocr_worker = OCRWorker(self.activity_db, self.buffer)
        self._clipboard_monitor = ClipboardMonitor(self.activity_db)

        # Spine dual-write: EventBus + SightModule
        self._event_bus = EventBus(self.activity_db.db_path)
        self._sight_module = SightModule()
        self._sight_module.register(self._event_bus.emit)
        self._sight_module.start()
        self._ocr_worker.set_sight_module(self._sight_module)
        self._clipboard_monitor.set_sight_module(self._sight_module)

    # -- Privacy guard -----------------------------------------------------

    def _should_skip(self, action_name: str) -> bool:
        if self.paused:
            logger.info("Paused -- skipping %s", action_name)
            return True
        if is_blocked():
            logger.info("Blocked window -- skipping %s", action_name)
            return True
        return False

    # -- Capture actions ---------------------------------------------------

    def do_quick_capture(self):
        if self._should_skip("quick capture"):
            return
        try:
            idx, img = capture.capture_active_monitor()
            capture.save_image(img, FILE_LATEST)
            self.buffer.add(img, monitor_index=idx)
        except Exception:
            logger.exception("Quick capture failed")

    def do_all_capture(self):
        if self._should_skip("all-monitor capture"):
            return
        try:
            monitors = capture.capture_all_monitors()
            for idx, img in monitors:
                path = OUTPUT_DIR / f"screen_monitor_{idx}.png"
                capture.save_image(img, path)
        except Exception:
            logger.exception("All-monitor capture failed")

    def do_region_capture(self):
        if self._should_skip("region capture"):
            return
        try:
            img = capture.capture_region()
            capture.save_image(img, FILE_REGION)
        except Exception:
            logger.exception("Region capture failed")

    def toggle_pause(self):
        self._user_paused = not self._user_paused
        self.paused = self._user_paused
        state = "PAUSED" if self.paused else "ACTIVE"
        logger.info("ContextPulse Sight %s", state)
        self._update_tray_icon()

    # -- Session lock/unlock -----------------------------------------------

    def _on_session_lock(self):
        logger.info("Session locked -- auto-pausing")
        self.paused = True
        self._sight_module.emit_session_lock(locked=True)
        self._update_tray_icon()

    def _on_session_unlock(self):
        logger.info("Session unlocked -- restoring state")
        self.paused = self._user_paused
        self._sight_module.emit_session_lock(locked=False)
        self._update_tray_icon()

    # -- Auto-capture loop -------------------------------------------------

    def _do_auto_capture(self):
        """Capture all monitors, store in buffer, record activity. Returns True on success."""
        monitors = capture.capture_all_monitors()
        cursor_idx = monitors[0][0] if monitors else 0
        try:
            import mss as _mss
            with _mss.mss() as sct:
                cursor_idx, _ = capture.find_monitor_at_cursor(sct)
        except Exception:
            pass

        # Get current window info for activity tracking
        window_title = get_foreground_window_title()
        app_name = get_foreground_process_name()
        if is_blocked():
            window_title = "[BLOCKED]"

        now = time.time()
        for idx, img in monitors:
            result = self.buffer.add(img, monitor_index=idx)
            if result:
                frame_path, diff_pct = result  # buffer.add returns (Path, diff_pct)
                # Record activity with diff score
                row_id = self.activity_db.record(
                    timestamp=now,
                    window_title=window_title,
                    app_name=app_name,
                    monitor_index=idx,
                    frame_path=str(frame_path) if frame_path else None,
                    diff_score=diff_pct,
                )
                # Dual-write: emit to EventBus via SightModule
                self._sight_module.emit_capture(
                    timestamp=now,
                    app_name=app_name,
                    window_title=window_title,
                    monitor_index=idx,
                    frame_path=str(frame_path) if frame_path else "",
                    diff_score=diff_pct,
                )
                # Queue for background OCR
                if frame_path and isinstance(frame_path, Path):
                    self._ocr_worker.enqueue(frame_path, row_id, app_name)

                if idx == cursor_idx:
                    capture.save_image(img, FILE_LATEST)
                    logger.debug(
                        "Frame stored m%d (%d in buffer)",
                        idx, self.buffer.frame_count(),
                    )

        # Prune old activity records alongside buffer pruning
        self.activity_db.prune()
        return True

    def _auto_capture_loop(self):
        logger.info("Auto-capture started (interval=%ds)", AUTO_INTERVAL)
        consecutive_errors = 0
        last_capture_time = 0.0
        while not self.stop_event.is_set():
            if not self._should_skip("auto-capture"):
                now = time.time()
                event_fired = self._event_detector.has_pending_event()
                timer_expired = (now - last_capture_time) >= AUTO_INTERVAL

                if event_fired or timer_expired:
                    try:
                        if event_fired:
                            reason = self._event_detector.get_pending_reason()
                            logger.debug("Event-driven capture: %s", reason)
                            self._event_detector.clear_pending()

                        self._do_auto_capture()
                        last_capture_time = time.time()
                        consecutive_errors = 0
                    except Exception:
                        consecutive_errors += 1
                        logger.exception(
                            "Auto-capture failed (%d consecutive)", consecutive_errors
                        )
                        if consecutive_errors >= 5:
                            backoff = min(30, AUTO_INTERVAL * consecutive_errors)
                            logger.warning(
                                "Too many capture errors, backing off %ds", backoff
                            )
                            self.stop_event.wait(backoff)
                            continue
            # Check more frequently than AUTO_INTERVAL to catch events promptly
            self.stop_event.wait(1)
        logger.info("Auto-capture stopped")

    # -- Watchdog ----------------------------------------------------------

    def _watchdog_loop(self):
        """Restart daemon threads if they die unexpectedly."""
        logger.info("Watchdog started (monitoring all threads)")
        while not self.stop_event.is_set():
            self.stop_event.wait(15)  # check every 15 seconds
            if self.stop_event.is_set():
                break
            # Auto-capture thread
            if hasattr(self, "_capture_thread") and not self._capture_thread.is_alive():
                logger.warning("Auto-capture thread died — restarting")
                self._capture_thread = threading.Thread(
                    target=self._auto_capture_loop, daemon=True
                )
                self._capture_thread.start()
            # Event detector
            if hasattr(self, "_event_detector") and self._event_detector is not None:
                if not self._event_detector.is_alive():
                    logger.warning("Event detector died — restarting")
                    try:
                        self._event_detector = EventDetector(self._on_event_capture, self.stop_event)
                        self._event_detector.start()
                    except Exception:
                        logger.exception("Failed to restart event detector")
            # OCR worker
            if hasattr(self, "_ocr_worker") and self._ocr_worker is not None:
                if not self._ocr_worker.is_alive():
                    logger.warning("OCR worker died — restarting")
                    try:
                        self._ocr_worker = OCRWorker(self.activity_db)
                        self._ocr_worker.start()
                    except Exception:
                        logger.exception("Failed to restart OCR worker")
            # Clipboard monitor
            if hasattr(self, "_clipboard_monitor") and self._clipboard_monitor is not None:
                if not self._clipboard_monitor.is_alive():
                    logger.warning("Clipboard monitor died — restarting")
                    try:
                        self._clipboard_monitor = ClipboardMonitor(self.activity_db)
                        self._clipboard_monitor.start()
                    except Exception:
                        logger.exception("Failed to restart clipboard monitor")
            # Hotkey listener
            if hasattr(self, "hotkey_listener") and not self.hotkey_listener.is_alive():
                logger.warning("Hotkey listener died — restarting")
                try:
                    self.hotkey_listener = keyboard.Listener(
                        on_press=self._on_press, on_release=self._on_release
                    )
                    self.hotkey_listener.start()
                except Exception:
                    logger.exception("Failed to restart hotkey listener")
        logger.info("Watchdog stopped")

    # -- Hotkey handling ---------------------------------------------------

    def _on_press(self, key):
        self._pressed_keys.add(key)
        self._check_hotkeys()

    def _on_release(self, key):
        self._pressed_keys.discard(key)

    def _get_key_letter(self, key) -> str | None:
        try:
            ch = key.char
            if ch is not None:
                code = ord(ch)
                if 1 <= code <= 26:
                    return chr(code + 96)
                return ch.lower()
        except AttributeError:
            pass
        try:
            vk = key.vk
            if vk is not None and 0x41 <= vk <= 0x5A:
                return chr(vk + 32)
        except AttributeError:
            pass
        return None

    def _check_hotkeys(self):
        ctrl = (keyboard.Key.ctrl_l in self._pressed_keys
                or keyboard.Key.ctrl_r in self._pressed_keys)
        shift = (keyboard.Key.shift_l in self._pressed_keys
                 or keyboard.Key.shift_r in self._pressed_keys)
        if not (ctrl and shift):
            return

        hotkey_map = {
            "s": lambda: threading.Thread(target=self.do_quick_capture, daemon=True).start(),
            "a": lambda: threading.Thread(target=self.do_all_capture, daemon=True).start(),
            "z": lambda: threading.Thread(target=self.do_region_capture, daemon=True).start(),
            "p": self.toggle_pause,
        }

        for key in list(self._pressed_keys):
            letter = self._get_key_letter(key)
            if letter and letter in hotkey_map:
                hotkey_map[letter]()
                self._pressed_keys.clear()
                return

    # -- System tray -------------------------------------------------------

    def _update_tray_icon(self):
        if hasattr(self, "tray") and self.tray:
            self.tray.icon = create_icon(_WARNING_COLOR if self.paused else None)

    def _create_tray_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                "Quick Capture (Ctrl+Shift+S)",
                lambda: threading.Thread(target=self.do_quick_capture, daemon=True).start(),
            ),
            pystray.MenuItem(
                "All Monitors (Ctrl+Shift+A)",
                lambda: threading.Thread(target=self.do_all_capture, daemon=True).start(),
            ),
            pystray.MenuItem(
                "Region (Ctrl+Shift+Z)",
                lambda: threading.Thread(target=self.do_region_capture, daemon=True).start(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: f"Buffer: {self.buffer.frame_count()} frames",
                lambda: None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda _: "Resume" if self.paused else "Pause",
                lambda: self.toggle_pause(),
            ),
            pystray.MenuItem(
                "Open Screenshots",
                lambda: subprocess.Popen(["explorer", str(OUTPUT_DIR)]),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Settings",
                lambda: threading.Thread(target=show_settings, daemon=True).start(),
            ),
            pystray.MenuItem(
                "Enter License Key",
                lambda: threading.Thread(target=show_nag_dialog, daemon=True).start(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _quit(self):
        logger.info("Shutting down")
        self.stop_event.set()
        self._event_detector.stop()
        self._ocr_worker.stop()
        self._clipboard_monitor.stop()
        self._sight_module.stop()
        self._event_bus.close()
        self.activity_db.close()
        # Clean up tkinter root used by settings/dialogs
        from contextpulse_core.gui_theme import destroy_root
        destroy_root()
        if hasattr(self, "hotkey_listener") and self.hotkey_listener:
            self.hotkey_listener.stop()
        if hasattr(self, "_mutex") and self._mutex:
            ctypes.windll.kernel32.ReleaseMutex(self._mutex)
            ctypes.windll.kernel32.CloseHandle(self._mutex)
        if hasattr(self, "tray") and self.tray:
            self.tray.stop()

    # -- Run ---------------------------------------------------------------

    def run(self):
        # Single-instance guard via Windows named mutex
        self._mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "ContextPulseSight_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            logger.error("ContextPulse Sight is already running. Exiting.")
            print("ContextPulse Sight is already running.", file=sys.stderr)
            sys.exit(1)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # First-run welcome dialog
        if is_first_run():
            logger.info("First run detected — showing welcome dialog")
            show_welcome_dialog()

        logger.info("ContextPulse Sight starting -- output: %s", OUTPUT_DIR)
        logger.info(
            "Auto-capture: every %ds, buffer: %ds, change threshold: %.1f%%",
            AUTO_INTERVAL, BUFFER_MAX_AGE, CHANGE_THRESHOLD,
        )

        self.hotkey_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self.hotkey_listener.start()

        self._session_monitor = SessionMonitor(
            on_lock=self._on_session_lock,
            on_unlock=self._on_session_unlock,
        )
        self._session_monitor.start()

        self._event_detector.start()
        self._ocr_worker.start()
        self._clipboard_monitor.start()

        if AUTO_INTERVAL > 0:
            self._capture_thread = threading.Thread(
                target=self._auto_capture_loop, daemon=True
            )
            self._capture_thread.start()

            # Watchdog restarts capture thread if it dies
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, daemon=True
            )
            self._watchdog_thread.start()

        self.tray = pystray.Icon(
            name="ContextPulse Sight",
            icon=create_icon(),
            title="ContextPulse Sight - Active",
            menu=self._create_tray_menu(),
        )
        logger.info("Tray icon ready. Hotkeys: Ctrl+Shift+S/A/Z/P")
        self.tray.run()


def main():
    import sys as _sys

    # Handle --setup flag for MCP config generation
    if "--setup" in _sys.argv:
        from contextpulse_sight.setup import setup_client, setup_all, print_config

        idx = _sys.argv.index("--setup")
        if idx + 1 < len(_sys.argv):
            target = _sys.argv[idx + 1]
            if target == "all":
                setup_all()
            elif target == "print":
                print_config()
            else:
                setup_client(target)
        else:
            print("Usage: contextpulse-sight --setup {claude-code|cursor|gemini|all|print}")
            _sys.exit(1)
        return

    try:
        logger.info("ContextPulse Sight starting (pid=%d)", __import__("os").getpid())
        app = ContextPulseSightApp()
        app.run()
    except Exception:
        logger.exception("Fatal error — daemon crashed")
        raise


if __name__ == "__main__":
    main()
