# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

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

import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

# Import _thread_caps FIRST so that OMP/MKL/OPENBLAS/NUMEXPR env vars are set
# before any contextpulse module transitively imports numpy or faster-whisper.
# Without this, each pool eagerly allocates ``cpu_count()`` worker threads at
# first import — see _thread_caps.py for the full incident write-up.
from contextpulse_core import _thread_caps  # noqa: F401  -- side-effect import; must be first

if sys.platform == "darwin":
    # rumps imported lazily in tray_macos.py
    pass
else:
    import pystray

from contextpulse_core.config import OUTPUT_DIR as _cfg_output_dir
from contextpulse_core.first_run import is_first_run, show_welcome_dialog
from contextpulse_core.license_dialog import show_nag_dialog
from contextpulse_core.platform import get_platform_provider
from contextpulse_core.settings import show_settings
from contextpulse_core.spine import EventBus

logger = logging.getLogger("contextpulse.daemon")

OUTPUT_DIR = _cfg_output_dir
LOG_FILE = OUTPUT_DIR / "contextpulse.log"
CRASH_LOG = OUTPUT_DIR / "contextpulse_crash.log"
ACTIVITY_DB_PATH = OUTPUT_DIR / os.environ.get("CONTEXTPULSE_ACTIVITY_DB", "activity.db")


def _setup_logging() -> None:
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

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self._running = True  # set False in _quit() so tray restart loop stops

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

    # ── Properties for tray integration (used by tray_macos) ────────

    @property
    def paused(self) -> bool:
        """Whether screen capture is currently paused."""
        return bool(self._sight_app and self._sight_app.paused)

    @property
    def output_dir(self) -> Path:
        return OUTPUT_DIR

    def toggle_pause(self) -> None:
        """Toggle Sight capture pause state."""
        if self._sight_app:
            self._sight_app.toggle_pause()

    def shutdown(self) -> None:
        """Graceful shutdown — stop modules and clean up."""
        self._quit()

    # ── Module Initialization ─────────────────────────────────────

    def _init_sight(self) -> None:
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

    def _init_voice(self) -> None:
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

    def _init_touch(self) -> None:
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

    def _start_modules(self) -> None:
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

    def _start_voice_with_progress(self) -> None:
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
            # Refresh tray tooltip now that voice is alive — without this,
            # the tooltip shows "voice=OFF" until the watchdog cycle (15s).
            self._update_tray()
        except Exception as exc:
            self._module_errors["voice"] = str(exc)
            self._log_crash("voice", exc)
            logger.exception("Voice module failed to start: %s", exc)

    def _stop_modules(self) -> None:
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

    def _watchdog_loop(self) -> None:
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
                logger.debug("Heartbeat write failed", exc_info=True)

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

            # Refresh tray tooltip so it reflects current module state
            self._update_tray()

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
                lambda: subprocess.Popen(
                    ["open", str(OUTPUT_DIR)] if sys.platform == "darwin"
                    else ["xdg-open", str(OUTPUT_DIR)] if sys.platform.startswith("linux")
                    else ["explorer", str(OUTPUT_DIR)]
                ),
            ),
            pystray.MenuItem(
                "Open Log",
                lambda: subprocess.Popen(
                    ["open", str(LOG_FILE)] if sys.platform == "darwin"
                    else ["xdg-open", str(LOG_FILE)] if sys.platform.startswith("linux")
                    else ["notepad", str(LOG_FILE)]
                ),
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

    def _update_tray(self) -> None:
        """Update tray icon and tooltip based on current module state."""
        if not hasattr(self, "tray") or not self.tray:
            return
        from contextpulse_sight.icon import _COLORS, create_icon
        warning = _COLORS.get("dark", {}).get("warning", "#F0B429")
        paused = self._sight_app and self._sight_app.paused
        self.tray.icon = create_icon(warning if paused else None)
        self.tray.title = self._get_status_text()

    def _quit(self) -> None:
        logger.info("ContextPulse shutting down")
        self._running = False
        self._stop_modules()

        # Clean up tkinter root
        try:
            from contextpulse_core.gui_theme import destroy_root
            destroy_root()
        except Exception:
            pass

        # Release mutex
        if hasattr(self, "_mutex") and self._mutex:
            get_platform_provider().release_single_instance_lock(self._mutex)

        if hasattr(self, "tray") and self.tray:
            self.tray.stop()

    # ── Main Entry ────────────────────────────────────────────────

    def run(self) -> None:
        """Main entry point — single-instance guard, start modules, run tray."""
        # Single-instance guard via platform provider
        platform = get_platform_provider()

        # Kill zombie ContextPulse processes before acquiring mutex.
        # These are leftover pythonw processes from previous crashes or
        # the mutex race condition (fixed in windows.py).
        my_pid = os.getpid()
        if hasattr(platform, "find_contextpulse_processes"):
            zombies = platform.find_contextpulse_processes(exclude_pid=my_pid)
            if zombies:
                logger.warning(
                    "Found %d zombie ContextPulse process(es): %s — killing",
                    len(zombies), zombies,
                )
                for pid in zombies:
                    platform.kill_process(pid)
                    logger.info("Killed zombie pid=%d", pid)
                # Brief pause to let the OS release the mutex
                import time
                time.sleep(0.5)

        self._mutex = platform.acquire_single_instance_lock("ContextPulse_SingleInstance")
        if self._mutex is None:
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

        # Rebuild context vocabulary from project directories (non-blocking).
        # This generates vocabulary_context.json with CamelCase project names
        # so Voice can correct "context pulse" → "ContextPulse" etc.
        try:
            from contextpulse_voice.context_vocab import rebuild_context_vocabulary
            count = rebuild_context_vocabulary()
            logger.info("Context vocabulary rebuilt: %d entries", count)
        except Exception:
            logger.debug("Context vocabulary rebuild skipped (voice not available)")

        # Start daemon watchdog for Voice + Touch
        self._daemon_watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._daemon_watchdog_thread.start()

        # System tray — platform-branched
        if sys.platform == "darwin":
            from contextpulse_core.tray_macos import create_tray
            self.tray = create_tray(self)
            logger.info("ContextPulse running — Sight + Voice + Touch (macOS menu bar)")
            self.tray.run()  # blocks on main thread (AppKit requirement)
        else:
            from contextpulse_sight.icon import create_icon
            self.tray = pystray.Icon(
                name="ContextPulse",
                icon=create_icon(),
                title=self._get_status_text(),
                menu=self._create_tray_menu(),
            )
            logger.info("ContextPulse running — Sight + Voice + Touch")

            # Keep-alive sentinel: pystray.Icon.run() can exit silently when
            # Windows rebuilds the notification area (explorer.exe restart,
            # WM_ENDSESSION, clipboard interaction).  We retry automatically
            # so the daemon doesn't die.
            _MAX_TRAY_RESTARTS = 5
            for _tray_attempt in range(_MAX_TRAY_RESTARTS):
                self.tray.run()  # blocks until quit or unexpected exit
                if not self._running:
                    break  # intentional quit via menu
                logger.warning(
                    "Tray exited unexpectedly (attempt %d/%d) — restarting",
                    _tray_attempt + 1, _MAX_TRAY_RESTARTS,
                )
                try:
                    from contextpulse_sight.icon import create_icon
                    self.tray = pystray.Icon(
                        name="ContextPulse",
                        icon=create_icon(),
                        title=self._get_status_text(),
                        menu=self._create_tray_menu(),
                    )
                except Exception:
                    logger.exception("Failed to recreate tray icon")
                    break
            else:
                logger.error("Tray restart limit reached — daemon exiting")


def main() -> None:
    """Entry point for contextpulse CLI command."""
    _setup_logging()

    # Handle --setup flag for MCP config + companion skills
    if "--setup" in sys.argv:
        from contextpulse_sight.setup import print_config, setup_all
        idx = sys.argv.index("--setup")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1] == "print":
            print_config()
        else:
            # Configure MCP servers
            setup_all()
            # Install companion skills
            print("\n--- Companion Skills ---")
            from contextpulse_core.skill_setup import install_skills
            force = "--force" in sys.argv
            install_skills("claude-code", force=force)
            install_skills("gemini", force=force)
            # Show ecosystem status
            from contextpulse_core.skill_setup import print_ecosystem_status
            print_ecosystem_status()
        return

    # Handle --status flag
    if "--status" in sys.argv:
        from contextpulse_core.skill_setup import print_ecosystem_status
        print_ecosystem_status()
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
