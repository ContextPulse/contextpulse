"""Main application: system tray + global hotkeys + auto-capture with rolling buffer."""

import ctypes
import logging
import subprocess
import sys
import threading

import pystray
from pynput import keyboard

from contextpulse_sight import capture
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.config import (
    AUTO_INTERVAL, BUFFER_MAX_AGE, CHANGE_THRESHOLD,
    FILE_ALL, FILE_LATEST, FILE_REGION, OUTPUT_DIR,
)
from contextpulse_sight.icon import _COLORS, create_icon
from contextpulse_sight.privacy import SessionMonitor, is_blocked

_WARNING_COLOR = _COLORS.get("dark", {}).get("warning", "#F0B429")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.sight")


class ContextPulseSightApp:
    def __init__(self):
        self.paused = False
        self._user_paused = False  # tracks manual pause vs auto-pause from lock
        self.stop_event = threading.Event()
        self._pressed_keys: set = set()
        self.buffer = RollingBuffer()

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
            img = capture.capture_active_monitor()
            capture.save_image(img, FILE_LATEST)
            self.buffer.add(img)
        except Exception:
            logger.exception("Quick capture failed")

    def do_all_capture(self):
        if self._should_skip("all-monitor capture"):
            return
        try:
            img = capture.capture_all_monitors()
            capture.save_image(img, FILE_ALL)
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
        self._update_tray_icon()

    def _on_session_unlock(self):
        logger.info("Session unlocked -- restoring state")
        self.paused = self._user_paused
        self._update_tray_icon()

    # -- Auto-capture loop -------------------------------------------------

    def _auto_capture_loop(self):
        logger.info("Auto-capture started (interval=%ds)", AUTO_INTERVAL)
        while not self.stop_event.is_set():
            if not self._should_skip("auto-capture"):
                try:
                    # Capture active monitor for buffer/change detection
                    img = capture.capture_active_monitor()
                    stored = self.buffer.add(img)
                    if stored:
                        capture.save_image(img, FILE_LATEST)
                        logger.debug("Frame stored (%d in buffer)", self.buffer.frame_count())
                    # Also capture all monitors so agents always have full context
                    img_all = capture.capture_all_monitors()
                    capture.save_image(img_all, FILE_ALL)
                except Exception:
                    logger.exception("Auto-capture failed")
            self.stop_event.wait(AUTO_INTERVAL)
        logger.info("Auto-capture stopped")

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
            pystray.MenuItem("Quit", self._quit),
        )

    def _quit(self):
        logger.info("Shutting down")
        self.stop_event.set()
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

        if AUTO_INTERVAL > 0:
            t = threading.Thread(target=self._auto_capture_loop, daemon=True)
            t.start()

        self.tray = pystray.Icon(
            name="ContextPulse Sight",
            icon=create_icon(),
            title="ContextPulse Sight - Active",
            menu=self._create_tray_menu(),
        )
        logger.info("Tray icon ready. Hotkeys: Ctrl+Shift+S/A/Z/P")
        self.tray.run()


def main():
    app = ContextPulseSightApp()
    app.run()


if __name__ == "__main__":
    main()
