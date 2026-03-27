"""ContextPulse Daemon — unified launcher for all modality modules.

Single process, single tray icon. Starts:
  - SightModule (screen capture + OCR + clipboard)
  - VoiceModule (hotkey → record → transcribe → paste)
  - TouchModule (keyboard bursts + mouse events + correction detection)

All modules emit to a shared EventBus backed by activity.db.

Production features:
  - Watchdog: restarts Voice/Touch if they die
  - Crash reporting: logs + tray notification on module failure
  - Model download: progress indicator for first-run Whisper download
"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import pystray

from contextpulse_core.first_run import is_first_run, show_welcome_dialog
from contextpulse_core.license import is_licensed
from contextpulse_core.license_dialog import show_nag_dialog
from contextpulse_core.settings import show_settings
from contextpulse_core.spine import EventBus

logger = logging.getLogger("contextpulse.daemon")

# Resolve output dir (same as Sight config)
OUTPUT_DIR = Path(os.environ.get(
    "CONTEXTPULSE_OUTPUT_DIR", str(Path.home() / "screenshots")
))
LOG_FILE = OUTPUT_DIR / "contextpulse.log"
CRASH_LOG = OUTPUT_DIR / "contextpulse_crash.log"
ACTIVITY_DB_PATH = OUTPUT_DIR / os.environ.get("CONTEXTPULSE_ACTIVITY_DB", "activity.db")


def _setup_logging():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


class ContextPulseDaemon:
    """Unified daemon that runs all ContextPulse modules in one process."""

    def __init__(self):
        self.stop_event = threading.Event()

        # EventBus — shared by all modules
        self._event_bus = EventBus(ACTIVITY_DB_PATH)

        # Module registry: (name, module)
        self._modules: list[tuple[str, object]] = []
        self._module_errors: dict[str, str] = {}
        self._restart_counts: dict[str, int] = {}  # watchdog restart tracking
        self._last_tray_notification: float = 0  # debounce tray notifications

        # Initialize modules
        self._sight_app = None
        self._voice_module = None
        self._touch_module = None

        self._init_sight()
        self._init_voice()
        self._init_touch()

    # ── Module Initialization ─────────────────────────────────────

    def _init_sight(self):
        """Initialize Sight (screen capture + OCR + clipboard)."""
        try:
            from contextpulse_sight.app import ContextPulseSightApp
            self._sight_app = ContextPulseSightApp()
            # Override its EventBus to use ours
            self._sight_app._event_bus.close()
            self._sight_app._event_bus = self._event_bus
            self._sight_app._sight_module.register(self._event_bus.emit)
            self._modules.append(("sight", self._sight_app))
            logger.info("Sight module initialized")
        except Exception as exc:
            self._module_errors["sight"] = str(exc)
            self._sight_app = None
            logger.exception("Sight module failed to initialize: %s", exc)

    def _init_voice(self):
        """Initialize Voice (hotkey → record → transcribe → paste)."""
        try:
            from contextpulse_voice.voice_module import VoiceModule
            self._voice_module = VoiceModule()
            self._voice_module.register(self._event_bus.emit)
            self._modules.append(("voice", self._voice_module))
            logger.info("Voice module initialized")
        except Exception as exc:
            self._module_errors["voice"] = str(exc)
            self._voice_module = None
            logger.exception("Voice module failed to initialize: %s", exc)

    def _init_touch(self):
        """Initialize Touch (keyboard + mouse input capture)."""
        try:
            from contextpulse_touch.touch_module import TouchModule
            self._touch_module = TouchModule(db_path=ACTIVITY_DB_PATH)
            self._touch_module.register(self._event_bus.emit)
            self._modules.append(("touch", self._touch_module))
            logger.info("Touch module initialized")
        except Exception as exc:
            self._module_errors["touch"] = str(exc)
            self._touch_module = None
            logger.exception("Touch module failed to initialize: %s", exc)

    # ── Module Lifecycle ──────────────────────────────────────────

    def _start_modules(self):
        """Start all initialized modules."""
        # Sight — has its own internal thread management
        if self._sight_app:
            self._sight_app._event_detector.start()
            self._sight_app._ocr_worker.start()
            self._sight_app._clipboard_monitor.start()
            self._sight_app._sight_module.start()

            from contextpulse_sight.privacy import SessionMonitor
            self._sight_app._session_monitor = SessionMonitor(
                on_lock=self._sight_app._on_session_lock,
                on_unlock=self._sight_app._on_session_unlock,
            )
            self._sight_app._session_monitor.start()

            from contextpulse_sight.config import AUTO_INTERVAL
            if AUTO_INTERVAL > 0:
                self._sight_app._capture_thread = threading.Thread(
                    target=self._sight_app._auto_capture_loop, daemon=True
                )
                self._sight_app._capture_thread.start()

                self._sight_app._watchdog_thread = threading.Thread(
                    target=self._sight_app._watchdog_loop, daemon=True
                )
                self._sight_app._watchdog_thread.start()

            # Sight hotkeys (Ctrl+Shift+S/A/Z/P)
            from pynput import keyboard
            self._sight_app.hotkey_listener = keyboard.Listener(
                on_press=self._sight_app._on_press,
                on_release=self._sight_app._on_release,
            )
            self._sight_app.hotkey_listener.start()

            logger.info("Sight module started (auto-capture, OCR, clipboard, hotkeys)")

        # Voice — start in background thread (model download can be slow)
        if self._voice_module:
            threading.Thread(
                target=self._start_voice_with_progress, daemon=True
            ).start()

        # Touch — starts keyboard + mouse listeners
        if self._touch_module:
            try:
                self._touch_module.start()
                logger.info("Touch module started (keyboard + mouse capture)")
            except Exception as exc:
                self._module_errors["touch"] = str(exc)
                logger.exception("Touch module failed to start: %s", exc)

    def _start_voice_with_progress(self):
        """Start Voice module with model download progress handling."""
        try:
            # Check if model needs downloading (frozen EXE only)
            if getattr(sys, "frozen", False):
                from contextpulse_voice.config import get_voice_config
                cfg = get_voice_config()
                model_size = cfg["whisper_model"]
                from contextpulse_voice.model_manager import MODEL_DIR
                model_dir = MODEL_DIR / f"faster-whisper-{model_size}"
                if not (model_dir / "model.bin").exists():
                    logger.info(
                        "First run: downloading Whisper '%s' model — "
                        "this may take a few minutes...", model_size
                    )
                    self._notify_tray(
                        "Downloading voice model",
                        f"Downloading Whisper {model_size} model for first-time setup. "
                        "Voice dictation will be available shortly."
                    )

            self._voice_module.start()
            logger.info("Voice module started (hotkey listener active)")
        except Exception as exc:
            self._module_errors["voice"] = str(exc)
            self._log_crash("voice", exc)
            logger.exception("Voice module failed to start: %s", exc)

    def _stop_modules(self):
        """Stop all modules gracefully."""
        self.stop_event.set()

        if self._sight_app:
            self._sight_app.stop_event.set()
            self._sight_app._event_detector.stop()
            self._sight_app._ocr_worker.stop()
            self._sight_app._clipboard_monitor.stop()
            self._sight_app._sight_module.stop()
            if hasattr(self._sight_app, "hotkey_listener") and self._sight_app.hotkey_listener:
                self._sight_app.hotkey_listener.stop()
            self._sight_app.activity_db.close()

        if self._voice_module:
            self._voice_module.stop()

        if self._touch_module:
            self._touch_module.stop()

        self._event_bus.close()
        logger.info("All modules stopped")

    # ── Watchdog ──────────────────────────────────────────────────

    def _watchdog_loop(self):
        """Monitor Voice and Touch modules, restart if they die.

        Sight has its own internal watchdog. This covers Voice and Touch.
        Max 3 restarts per module before giving up.

        Also writes a heartbeat file so external monitors (MCP tools,
        health checks) can detect if the daemon is alive without needing
        to probe the process directly.

        Additionally monitors for "stuck paused" state: if Sight has been
        paused for longer than expected (e.g. session monitor died and
        never delivered the unlock event), it forces an un-pause.
        """
        MAX_RESTARTS = 3
        STUCK_PAUSE_THRESHOLD = 3600  # 1 hour — if paused this long, likely stuck
        heartbeat_path = OUTPUT_DIR / "heartbeat"
        logger.info("Daemon watchdog started (monitoring Voice + Touch + heartbeat + stuck-pause)")

        while not self.stop_event.is_set():
            self.stop_event.wait(15)  # check every 15 seconds
            if self.stop_event.is_set():
                break

            # Write heartbeat file (timestamp) for external health checks
            try:
                heartbeat_path.write_text(str(time.time()), encoding="utf-8")
            except Exception:
                pass  # best-effort

            # Stuck-pause detection: if Sight has been paused but the user
            # didn't manually pause, the session monitor may have died.
            # After STUCK_PAUSE_THRESHOLD seconds, force un-pause.
            if self._sight_app and self._sight_app.paused and not self._sight_app._user_paused:
                if not hasattr(self, "_pause_detected_at"):
                    self._pause_detected_at = time.time()
                elif time.time() - self._pause_detected_at > STUCK_PAUSE_THRESHOLD:
                    logger.warning(
                        "Sight has been auto-paused for >%ds without unlock — "
                        "forcing un-pause (session monitor may have died)",
                        STUCK_PAUSE_THRESHOLD,
                    )
                    self._sight_app.paused = False
                    self._pause_detected_at = None
                    self._notify_tray(
                        "Auto-resumed capture",
                        "Screen capture was stuck paused. Resumed automatically."
                    )
            else:
                self._pause_detected_at = None  # type: ignore[attr-defined]

            # Voice watchdog
            if self._voice_module and not self._voice_module.is_alive():
                count = self._restart_counts.get("voice", 0)
                if count < MAX_RESTARTS:
                    logger.warning("Voice module died — restarting (attempt %d/%d)",
                                   count + 1, MAX_RESTARTS)
                    try:
                        from contextpulse_voice.voice_module import VoiceModule
                        self._voice_module = VoiceModule()
                        self._voice_module.register(self._event_bus.emit)
                        self._voice_module.start()
                        self._restart_counts["voice"] = count + 1
                        # Update module list
                        self._modules = [(n, m) for n, m in self._modules if n != "voice"]
                        self._modules.append(("voice", self._voice_module))
                        self._module_errors.pop("voice", None)
                        logger.info("Voice module restarted successfully")
                    except Exception as exc:
                        self._module_errors["voice"] = str(exc)
                        self._log_crash("voice", exc)
                        logger.exception("Voice module restart failed: %s", exc)
                elif count == MAX_RESTARTS:
                    logger.error("Voice module exceeded max restarts (%d) — giving up", MAX_RESTARTS)
                    self._restart_counts["voice"] = count + 1  # prevent repeated log
                    self._notify_tray(
                        "Voice module stopped",
                        "Voice dictation is unavailable. Check the log for details."
                    )

            # Touch watchdog
            if self._touch_module and not self._touch_module.is_alive():
                count = self._restart_counts.get("touch", 0)
                if count < MAX_RESTARTS:
                    logger.warning("Touch module died — restarting (attempt %d/%d)",
                                   count + 1, MAX_RESTARTS)
                    try:
                        from contextpulse_touch.touch_module import TouchModule
                        self._touch_module = TouchModule(db_path=ACTIVITY_DB_PATH)
                        self._touch_module.register(self._event_bus.emit)
                        self._touch_module.start()
                        self._restart_counts["touch"] = count + 1
                        self._modules = [(n, m) for n, m in self._modules if n != "touch"]
                        self._modules.append(("touch", self._touch_module))
                        self._module_errors.pop("touch", None)
                        logger.info("Touch module restarted successfully")
                    except Exception as exc:
                        self._module_errors["touch"] = str(exc)
                        self._log_crash("touch", exc)
                        logger.exception("Touch module restart failed: %s", exc)
                elif count == MAX_RESTARTS:
                    logger.error("Touch module exceeded max restarts (%d) — giving up", MAX_RESTARTS)
                    self._restart_counts["touch"] = count + 1
                    self._notify_tray(
                        "Touch module stopped",
                        "Keyboard/mouse capture is unavailable. Check the log for details."
                    )

        logger.info("Daemon watchdog stopped")

    # ── Crash Reporting ───────────────────────────────────────────

    def _log_crash(self, module_name: str, exc: Exception):
        """Write crash details to crash log file."""
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Module: {module_name}\n")
                f.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Error: {exc}\n")
                f.write(traceback.format_exc())
                f.write(f"\n{'='*60}\n")
        except Exception:
            pass  # Don't crash the crash reporter

    def _notify_tray(self, title: str, message: str):
        """Show a tray notification (debounced to 1 per 30 seconds)."""
        now = time.time()
        if now - self._last_tray_notification < 30:
            return
        self._last_tray_notification = now
        try:
            if hasattr(self, "tray") and self.tray:
                self.tray.notify(message, title)
        except Exception:
            pass  # Tray notification is best-effort

    # ── Status & Tray ─────────────────────────────────────────────

    def _get_status_text(self) -> str:
        """Build status string for tray tooltip."""
        parts = ["ContextPulse"]
        for name, mod in self._modules:
            alive = False
            if name == "sight" and hasattr(mod, "_sight_module"):
                alive = mod._sight_module.is_alive()
            elif hasattr(mod, "is_alive") and callable(mod.is_alive):
                alive = mod.is_alive()
            status = "ON" if alive else "OFF"
            parts.append(f"{name}={status}")
        if self._module_errors:
            for name, err in self._module_errors.items():
                if name not in [n for n, _ in self._modules]:
                    parts.append(f"{name}=ERR")
        return " | ".join(parts)

    def _create_tray_menu(self):
        from contextpulse_sight.icon import _COLORS
        _WARNING_COLOR = _COLORS.get("dark", {}).get("warning", "#F0B429")

        def _toggle_sight():
            if self._sight_app:
                self._sight_app.toggle_pause()
                self._update_tray()

        return pystray.Menu(
            pystray.MenuItem(
                lambda _: self._get_status_text(),
                lambda: None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: "Resume Capture" if (self._sight_app and self._sight_app.paused) else "Pause Capture",
                lambda: _toggle_sight(),
            ),
            pystray.MenuItem(
                "Open Screenshots",
                lambda: subprocess.Popen(["explorer", str(OUTPUT_DIR)]),
            ),
            pystray.MenuItem(
                "Open Log",
                lambda: subprocess.Popen(["notepad", str(LOG_FILE)]),
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

    def _update_tray(self):
        """Update tray icon based on state."""
        if not hasattr(self, "tray") or not self.tray:
            return
        from contextpulse_sight.icon import create_icon, _COLORS
        warning = _COLORS.get("dark", {}).get("warning", "#F0B429")
        paused = self._sight_app and self._sight_app.paused
        self.tray.icon = create_icon(warning if paused else None)

    def _quit(self):
        logger.info("ContextPulse shutting down")
        self._stop_modules()

        # Clean up tkinter root
        try:
            from contextpulse_core.gui_theme import destroy_root
            destroy_root()
        except Exception:
            pass

        # Release mutex
        if hasattr(self, "_mutex") and self._mutex:
            ctypes.windll.kernel32.ReleaseMutex(self._mutex)
            ctypes.windll.kernel32.CloseHandle(self._mutex)

        if hasattr(self, "tray") and self.tray:
            self.tray.stop()

    # ── Main Entry ────────────────────────────────────────────────

    def run(self):
        """Main entry point — single-instance guard, start modules, run tray."""
        # Single-instance mutex
        self._mutex = ctypes.windll.kernel32.CreateMutexW(
            None, True, "ContextPulse_SingleInstance"
        )
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            logger.error("ContextPulse is already running. Exiting.")
            print("ContextPulse is already running.", file=sys.stderr)
            sys.exit(1)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # First-run welcome
        if is_first_run():
            logger.info("First run — showing welcome dialog")
            show_welcome_dialog()

        logger.info("ContextPulse starting (pid=%d)", os.getpid())
        logger.info("Modules: %s", ", ".join(n for n, _ in self._modules))
        if self._module_errors:
            logger.warning("Module errors: %s", self._module_errors)

        # Start all modules
        self._start_modules()

        # Start daemon watchdog for Voice + Touch
        self._daemon_watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._daemon_watchdog_thread.start()

        # System tray
        from contextpulse_sight.icon import create_icon
        self.tray = pystray.Icon(
            name="ContextPulse",
            icon=create_icon(),
            title=self._get_status_text(),
            menu=self._create_tray_menu(),
        )
        logger.info("ContextPulse running — Sight + Voice + Touch")
        self.tray.run()  # blocks until quit


def main():
    """Entry point for contextpulse CLI command."""
    _setup_logging()

    # Handle --setup flag for MCP config
    if "--setup" in sys.argv:
        from contextpulse_sight.setup import setup_all, print_config
        idx = sys.argv.index("--setup")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1] == "print":
            print_config()
        else:
            setup_all()
        return

    try:
        logger.info("ContextPulse daemon starting (pid=%d)", os.getpid())
        daemon = ContextPulseDaemon()
        daemon.run()
    except MemoryError:
        logger.error("Fatal MemoryError — forcing GC and writing crash log")
        import gc
        gc.collect()
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"FATAL MemoryError: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(traceback.format_exc())
                f.write(f"\n{'='*60}\n")
        except Exception:
            pass
        raise
    except Exception:
        logger.exception("Fatal error — daemon crashed")
        # Write crash to separate file for easy discovery
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"FATAL DAEMON CRASH: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(traceback.format_exc())
                f.write(f"\n{'='*60}\n")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
