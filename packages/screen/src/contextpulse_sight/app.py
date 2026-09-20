# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Main application: system tray + global hotkeys + auto-capture with rolling buffer."""

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray

# Core productization imports (settings, first-run, licensing)
from contextpulse_core.config import _DEFAULTS
from contextpulse_core.config import get as cfg_get
from contextpulse_core.first_run import is_first_run, show_welcome_dialog
from contextpulse_core.license_dialog import show_nag_dialog
from contextpulse_core.log_rotation import rotating_file_handler
from contextpulse_core.platform import get_platform_provider
from contextpulse_core.settings import show_settings
from contextpulse_core.spine import EventBus
from pynput import keyboard

from contextpulse_sight import capture
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.clipboard import ClipboardMonitor
from contextpulse_sight.config import (
    FILE_LATEST,
    FILE_REGION,
    OUTPUT_DIR,
)
from contextpulse_sight.events import EventDetector
from contextpulse_sight.icon import _COLORS, create_icon
from contextpulse_sight.ocr_worker import OCRWorker
from contextpulse_sight.privacy import (
    SessionMonitor,
    get_foreground_process_name,
    get_foreground_window_title,
    is_blocked,
)
from contextpulse_sight.sight_module import SightModule

_WARNING_COLOR = _COLORS.get("dark", {}).get("warning", "#F0B429")

# -- Hotkeys ---------------------------------------------------------------
# The four hotkey_* keys were declared in config, shown in the Settings
# dialog, saved to config.json -- and read by nothing: the dispatch table
# below was a literal {"s": ..., "a": ..., "z": ..., "p": ...} and the tray
# labels were the string "(Ctrl+Shift+S)". Changing a hotkey in Settings
# changed the file and nothing else (dead-controls ledger rows 11-14).
#
# Deliberately local to app.py. Voice has its own _parse_hotkey which returns
# pynput key SETS for hold-to-talk -- a different model (press-and-hold vs
# fire-on-press), so sharing one parser would mean serving two semantics from
# one function.

# Order fixed so a formatted label reads Ctrl+Shift+Alt, never Shift+Ctrl.
_HOTKEY_MODIFIERS: tuple[str, ...] = ("ctrl", "shift", "alt")

# pynput Key attribute names per modifier; the first one found pressed wins.
_MODIFIER_KEY_NAMES: dict[str, tuple[str, ...]] = {
    "ctrl": ("ctrl_l", "ctrl_r"),
    "shift": ("shift_l", "shift_r"),
    "alt": ("alt_l", "alt_r", "alt_gr"),
}

# config key -> the action it fires. Order is the tray-menu order.
_HOTKEY_KEYS: tuple[str, ...] = (
    "hotkey_capture",
    "hotkey_all_monitors",
    "hotkey_region",
    "hotkey_pause",
)


def _parse_hotkey_spec(spec: str) -> tuple[frozenset[str], str] | None:
    """Parse "ctrl+shift+s" into (frozenset{"ctrl","shift"}, "s").

    Returns None for anything unusable: an unknown modifier, a key that is
    not a single a-z letter, or a spec with no modifier at all (a bare letter
    would fire on every keystroke, which is worse than ignoring it).
    """
    tokens = [t.strip().lower() for t in str(spec).split("+") if t.strip()]
    if len(tokens) < 2:
        return None
    *mods, letter = tokens
    if len(letter) != 1 or not ("a" <= letter <= "z"):
        return None
    if any(m not in _HOTKEY_MODIFIERS for m in mods):
        return None
    return frozenset(mods), letter


def parse_hotkey(spec: str, default: str) -> tuple[frozenset[str], str]:
    """Parse a hotkey spec, falling back to `default` with a warning."""
    parsed = _parse_hotkey_spec(spec)
    if parsed is not None:
        return parsed
    logger.warning(
        "Hotkey %r is not usable (expected modifier+letter, e.g. %r) -- using %r",
        spec, default, default,
    )
    fallback = _parse_hotkey_spec(default)
    if fallback is None:  # only reachable if _DEFAULTS itself is malformed
        raise ValueError(f"default hotkey {default!r} is itself unparseable")
    return fallback


def format_hotkey(mods: frozenset[str], letter: str) -> str:
    """Render a parsed hotkey for a menu label: "Ctrl+Shift+S"."""
    ordered = [m.capitalize() for m in _HOTKEY_MODIFIERS if m in mods]
    return "+".join([*ordered, letter.upper()])


def should_run_ocr(diff_pct: float, force_ocr: bool, threshold: float) -> bool:
    """Return True if a stored frame should be enqueued for OCR.

    The capture loop stores any frame above the buffer ``CHANGE_THRESHOLD``
    (default 0.5%). On idle screens that includes cursor blinks and clock
    ticks — frames that match the previous one almost everywhere. OCR'ing
    each one costs 0.2-0.7s of CPU per monitor per cycle, which sustained
    20-40% of one core. This gate skips OCR for those near-identical
    frames; the visual buffer still records them for replay.

    The ``force_ocr`` flag lets event-driven captures (window switch, app
    focus change) bypass the gate so meaningful UI transitions are always
    indexed even if the pixel diff happens to be small.

    Setting ``threshold`` to 0 disables the gate (always OCR), restoring
    pre-2026-04-29 behavior.
    """
    if force_ocr:
        return True
    return diff_pct >= threshold


def effective_interval(
    now: float,
    last_active_time: float,
    base_interval: float,
    idle_interval: float,
    idle_threshold: float,
) -> float:
    """Return the capture interval to use given recent activity.

    When events fired recently (within ``idle_threshold`` seconds), use
    ``base_interval`` for snappy response. When the user has been quiet
    longer than ``idle_threshold``, stretch to ``idle_interval`` to drop
    background CPU. The next event fires an immediate capture (the loop
    sets ``last_active_time = now`` on every event), so snap-back is one
    cycle of latency rather than an idle-interval wait.

    Defensive: if a misconfigured ``idle_interval`` is *lower* than
    ``base_interval``, fall back to ``base_interval`` — extending the
    interval is the only direction that makes sense here.
    """
    effective_idle = max(base_interval, idle_interval)
    idle_secs = now - last_active_time
    if idle_secs >= idle_threshold:
        return effective_idle
    return base_interval


_LOG_FILE = OUTPUT_DIR / "contextpulse_sight.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        rotating_file_handler(_LOG_FILE),
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
        # `clipboard_enabled` was declared in SightModule.get_config_schema and
        # read nowhere: the monitor was constructed here unconditionally and
        # restarted unconditionally by the watchdog, so the toggle a user could
        # see did nothing. None means "user turned clipboard capture off" and
        # every lifecycle site below is written to tolerate it.
        self._clipboard_monitor = (
            ClipboardMonitor(self.activity_db) if self._clipboard_enabled() else None
        )

        # Hotkeys are startup-bound by design: pynput binds a listener once
        # and the Settings dialog already tells the user a restart is needed.
        # Parsed here so the dispatch table AND the tray labels come from the
        # same values -- the labels were hardcoded strings that could not
        # disagree with the dispatch table because neither read the config.
        self._hotkeys: dict[str, tuple[frozenset[str], str]] = {
            key: parse_hotkey(cfg_get(key, _DEFAULTS[key]), _DEFAULTS[key])
            for key in _HOTKEY_KEYS
        }
        # Logged HERE rather than at tray-ready, because this is where the
        # chords are decided and because a headless/watchdog launch that never
        # reaches the tray still needs to say what it bound. Modifier matching
        # is EXACT (see _check_hotkeys), which is the one change on this
        # branch a user can feel without opening Settings: Ctrl+Shift+Alt+S no
        # longer fires Quick Capture the way a subset test let it. The
        # semantics stay -- a subset test lets one chord fire two bindings and
        # dict order decides which -- so the fix for "why did my muscle memory
        # stop working" is a line naming the four effective bindings.
        logger.info(
            "Hotkeys bound (modifiers must match exactly): %s",
            ", ".join(f"{k.removeprefix('hotkey_')}={format_hotkey(*v)}"
                      for k, v in self._hotkeys.items()),
        )
        self._hotkey_actions = {
            "hotkey_capture": lambda: self._in_thread(self.do_quick_capture),
            "hotkey_all_monitors": lambda: self._in_thread(self.do_all_capture),
            "hotkey_region": lambda: self._in_thread(self.do_region_capture),
            "hotkey_pause": self.toggle_pause,
        }

        # Spine dual-write: EventBus + SightModule
        self._event_bus = EventBus(self.activity_db.db_path)
        self._sight_module = SightModule()
        self._sight_module.register(self._event_bus.emit)
        self._sight_module.start()
        self._ocr_worker.set_sight_module(self._sight_module)
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.set_sight_module(self._sight_module)

    # -- Clipboard monitor lifecycle ---------------------------------------
    # All four sites (construct, start, watchdog restart, stop) go through
    # these so the setting cannot be honoured in one place and ignored in
    # another -- which is how it came to be dead in the first place.

    def _clipboard_enabled(self) -> bool:
        """Whether clipboard capture is switched on. Defaults to on."""
        return bool(cfg_get("clipboard_enabled", True))

    def _start_clipboard_monitor(self) -> None:
        """Start the clipboard monitor, if the user has it enabled.

        Also starts the reconcile timer, and starts it UNCONDITIONALLY --
        including when the monitor itself is not started, because the setting
        can be switched back on mid-session and nothing else would notice.
        """
        self._start_clipboard_reconcile_thread()
        if not self._clipboard_enabled():
            logger.info("Clipboard capture disabled by setting -- monitor not started")
            self._clipboard_monitor = None
            return
        if self._clipboard_monitor is None:
            self._clipboard_monitor = ClipboardMonitor(self.activity_db)
            self._clipboard_monitor.set_sight_module(self._sight_module)
        elif self._clipboard_monitor.is_alive():
            # Already running. Calling start() again raises "threads can only
            # be started once" -- found by a test that calls this twice, which
            # is now a reachable sequence: the daemon calls it and so does the
            # app's own run().
            return
        self._clipboard_monitor.start()

    def _start_clipboard_reconcile_thread(self) -> None:
        """Poll the clipboard setting on a dedicated timer.

        _reconcile_clipboard_monitor was only ever called from _watchdog_loop,
        and BOTH start sites -- this app's run() and the unified daemon's
        _start_modules -- wrap that watchdog in `if AUTO_INTERVAL > 0`. So a
        user who set CONTEXTPULSE_AUTO_INTERVAL=0 could untick "Capture
        clipboard contents" and the monitor kept polling and storing until the
        next restart (review S4) -- exactly the half-honoured behaviour the
        reconcile was written to prevent.

        A privacy control must not depend on an unrelated capture setting. This
        is a small dedicated thread rather than a change to the capture loop:
        the config-unification work owns that restructuring, and a timer that
        does one thing is easier to delete when it arrives.

        Idempotent -- the watchdog still calls reconcile too when it runs, and
        reconcile is safe to call repeatedly.
        """
        existing = getattr(self, "_clipboard_reconcile_thread", None)
        if existing is not None and existing.is_alive():
            return
        self._clipboard_reconcile_thread = threading.Thread(
            target=self._clipboard_reconcile_loop,
            name="cp-clipboard-reconcile",
            daemon=True,
        )
        self._clipboard_reconcile_thread.start()

    def _clipboard_reconcile_loop(self) -> None:
        while not self.stop_event.wait(15):
            try:
                self._reconcile_clipboard_monitor()
            except Exception:
                # A reconcile failure must not kill the loop -- the next tick
                # is the recovery, and a dead timer silently restores the bug.
                logger.exception("Clipboard reconcile failed")

    def _reconcile_clipboard_monitor(self) -> None:
        """Watchdog hook: make the running state match the setting.

        Reconciles in BOTH directions. Only restarting a dead monitor would
        leave the setting half-honoured -- unticking the box mid-session would
        not stop capture until the next daemon restart, which is the wrong way
        round for a privacy control.
        """
        if not self._clipboard_enabled():
            if self._clipboard_monitor is not None:
                logger.info("Clipboard capture switched off — stopping monitor")
                self._clipboard_monitor.stop()
                self._clipboard_monitor = None
            return
        if self._clipboard_monitor is not None and self._clipboard_monitor.is_alive():
            return
        logger.warning("Clipboard monitor died — restarting")
        try:
            self._clipboard_monitor = ClipboardMonitor(self.activity_db)
            self._clipboard_monitor.set_sight_module(self._sight_module)
            self._clipboard_monitor.start()
        except Exception:
            logger.exception("Failed to restart clipboard monitor")

    def _stop_clipboard_monitor(self) -> None:
        """Stop the clipboard monitor. Safe when it was never constructed."""
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()

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
            capture.save_image(img, FILE_LATEST, fmt="JPEG")
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

    def _do_auto_capture(self, force_ocr: bool = False):
        """Capture all monitors, store in buffer, record activity. Returns True on success.

        Args:
            force_ocr: When True, every stored frame is OCR'd regardless of
                diff_pct. Set by the auto-capture loop on event-driven runs
                (window switch, app focus change) so meaningful UI changes
                are always indexed even when their pixel diff is small.
        """
        monitors = capture.capture_all_monitors(keep_native=True)
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

        # Read once per capture cycle rather than per monitor, so every frame
        # in one cycle is judged against the same threshold.
        ocr_diff_threshold = float(
            cfg_get("ocr_diff_threshold", _DEFAULTS["ocr_diff_threshold"])
        )

        now = time.time()
        for idx, img, native_img in monitors:
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
                # Queue for background OCR — pass native-res image for
                # higher OCR accuracy (buffer stores downscaled JPEG).
                # Skip OCR on near-identical frames (cursor blinks, clock
                # ticks) per ocr_diff_threshold; force_ocr=True bypasses
                # the gate for event-driven captures.
                if frame_path and isinstance(frame_path, Path):
                    if should_run_ocr(diff_pct, force_ocr, ocr_diff_threshold):
                        # window_title/app_name were read ONCE above for the
                        # system-wide foreground window, which is only what
                        # THIS frame shows when idx is the cursor monitor.
                        # Attributing a non-cursor monitor's OCR text to the
                        # foreground app is false (cp-ocr-crossmonitor-
                        # mislabeled-attribution) -- suppress it there rather
                        # than propagate the wrong app/window downstream.
                        if idx == cursor_idx:
                            ocr_app_name, ocr_window_title = app_name, window_title
                        else:
                            ocr_app_name, ocr_window_title = "", ""
                        self._ocr_worker.enqueue(
                            frame_path, row_id, ocr_app_name,
                            window_title=ocr_window_title,
                            native_img=native_img,
                            monitor_index=idx,
                        )
                    else:
                        logger.debug(
                            "OCR skipped m%d (diff=%.1f%% < %.1f%%)",
                            idx, diff_pct, ocr_diff_threshold,
                        )

                if idx == cursor_idx:
                    capture.save_image(img, FILE_LATEST, fmt="JPEG")
                    logger.debug(
                        "Frame stored m%d (%d in buffer)",
                        idx, self.buffer.frame_count(),
                    )

        # Prune old activity records alongside buffer pruning
        self.activity_db.prune()
        return True

    def _intervals(self) -> tuple[float, float, float]:
        """(auto_interval, auto_interval_idle, auto_idle_threshold), read live.

        Called once per loop iteration -- the loop already ticks every second,
        so this costs one stat() per second and is what makes all three
        settable without a restart, including 0 -> N and N -> 0.
        """
        return (
            float(cfg_get("auto_interval", _DEFAULTS["auto_interval"])),
            float(cfg_get("auto_interval_idle", _DEFAULTS["auto_interval_idle"])),
            float(cfg_get("auto_idle_threshold", _DEFAULTS["auto_idle_threshold"])),
        )

    def _auto_capture_loop(self):
        base_interval, idle_interval, idle_threshold = self._intervals()
        logger.info(
            "Auto-capture started (active=%ds, idle=%ds after %ds quiet)",
            base_interval, idle_interval, idle_threshold,
        )
        consecutive_errors = 0
        last_capture_time = 0.0
        last_active_time = time.time()  # any event resets this
        last_logged_mode = "active"  # debounce mode-transition logs
        _last_paused_log = 0.0  # debounce "paused" log messages
        while not self.stop_event.is_set():
            base_interval, idle_interval, idle_threshold = self._intervals()
            # auto_interval 0 means "no automatic capture". It is handled HERE
            # rather than by declining to start the thread, which is what both
            # start sites used to do: a thread that was never started cannot
            # notice the user setting the interval back to 5, so 0 was a
            # one-way trip until the next daemon restart. Same quiet branch as
            # paused/blocked -- the thread stays alive and re-reads.
            if base_interval <= 0 or self._should_skip_quiet():
                # When off/paused/blocked, sleep longer to avoid log spam and
                # CPU waste. Log at most once per 60 seconds, not every second.
                now = time.time()
                if now - _last_paused_log >= 60:
                    if base_interval <= 0:
                        logger.info("Auto-capture off (auto_interval=0; re-checking every 5s)")
                    else:
                        logger.info("Auto-capture paused (will check every 5s)")
                    _last_paused_log = now
                self.stop_event.wait(5)
                continue
            now = time.time()
            event_fired = self._event_detector.has_pending_event()

            # Adaptive interval: stretch to auto_interval_idle after
            # auto_idle_threshold seconds of no events; snap back to
            # auto_interval on any event. (Phase A3, 2026-04-29.)
            current_interval = effective_interval(
                now=now,
                last_active_time=last_active_time,
                base_interval=base_interval,
                idle_interval=idle_interval,
                idle_threshold=idle_threshold,
            )
            mode = "idle" if current_interval > base_interval else "active"
            if mode != last_logged_mode:
                logger.info(
                    "Auto-capture mode -> %s (interval=%.0fs)", mode, current_interval,
                )
                last_logged_mode = mode

            timer_expired = (now - last_capture_time) >= current_interval

            if event_fired or timer_expired:
                try:
                    if event_fired:
                        reason = self._event_detector.get_pending_reason()
                        logger.debug("Event-driven capture: %s", reason)
                        self._event_detector.clear_pending()
                        last_active_time = now  # reset idle timer

                    # Event-driven captures always OCR (window switch / app
                    # focus change is meaningful even with small pixel diff).
                    # Timer-driven captures rely on the OCR_DIFF_THRESHOLD gate.
                    self._do_auto_capture(force_ocr=event_fired)
                    last_capture_time = time.time()
                    consecutive_errors = 0
                except MemoryError:
                    consecutive_errors += 1
                    logger.error(
                        "MemoryError during capture (%d consecutive) — forcing GC",
                        consecutive_errors,
                    )
                    import gc
                    gc.collect()
                    # Back off significantly on memory pressure
                    self.stop_event.wait(min(60, 10 * consecutive_errors))
                    continue
                except Exception:
                    consecutive_errors += 1
                    logger.exception(
                        "Auto-capture failed (%d consecutive)", consecutive_errors
                    )
                    if consecutive_errors >= 5:
                        backoff = min(30, base_interval * consecutive_errors)
                        logger.warning(
                            "Too many capture errors, backing off %ds", backoff
                        )
                        self.stop_event.wait(backoff)
                        continue
            # Check more frequently than the interval to catch events promptly
            self.stop_event.wait(1)
        logger.info("Auto-capture stopped")

    def _should_skip_quiet(self) -> bool:
        """Like _should_skip but without logging — for use in tight loops."""
        if self.paused:
            return True
        if is_blocked():
            return True
        return False

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
            if hasattr(self, "_clipboard_monitor"):
                self._reconcile_clipboard_monitor()
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
        """The a-z letter a key represents, or None if it is not one.

        TypeError/ValueError are caught alongside AttributeError because this
        runs inside the pynput on_press callback: anything raised here kills
        the listener thread, and every hotkey with it, for a key that simply
        was not a letter. ord() raises TypeError for a non-string .char and
        ValueError for a multi-character one.
        """
        try:
            ch = key.char
            if ch is not None:
                code = ord(ch)
                if 1 <= code <= 26:
                    return chr(code + 96)
                return ch.lower()
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            vk = key.vk
            if vk is not None and 0x41 <= vk <= 0x5A:
                return chr(vk + 32)
        except (AttributeError, TypeError, ValueError):
            pass
        return None

    def _in_thread(self, target) -> None:
        threading.Thread(target=target, daemon=True).start()

    def _pressed_modifiers(self) -> frozenset[str]:
        """Which of ctrl/shift/alt are currently held."""
        held = set()
        for mod, names in _MODIFIER_KEY_NAMES.items():
            for name in names:
                key = getattr(keyboard.Key, name, None)
                if key is not None and key in self._pressed_keys:
                    held.add(mod)
                    break
        return frozenset(held)

    def _check_hotkeys(self):
        """Fire the action whose configured hotkey is exactly what is held.

        The modifier set must match exactly, rather than merely contain the
        configured ones: with four user-settable combos, a subset test would
        let ctrl+shift+alt+s fire the ctrl+shift+s action AND any ctrl+alt+s
        binding at once, and which one won would depend on dict order.
        """
        pressed_mods = self._pressed_modifiers()
        if not pressed_mods:
            return

        for key in list(self._pressed_keys):
            letter = self._get_key_letter(key)
            if not letter:
                continue
            for name, (mods, hotkey_letter) in self._hotkeys.items():
                if hotkey_letter == letter and mods == pressed_mods:
                    self._hotkey_actions[name]()
                    self._pressed_keys.clear()
                    return

    # -- System tray -------------------------------------------------------

    def _update_tray_icon(self):
        if hasattr(self, "tray") and self.tray:
            self.tray.icon = create_icon(_WARNING_COLOR if self.paused else None)

    def _hotkey_label(self, key: str) -> str:
        """"(Ctrl+Shift+S)" for the tray, built from the configured hotkey."""
        return f"({format_hotkey(*self._hotkeys[key])})"

    def _create_tray_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                f"Quick Capture {self._hotkey_label('hotkey_capture')}",
                lambda: self._in_thread(self.do_quick_capture),
            ),
            pystray.MenuItem(
                f"All Monitors {self._hotkey_label('hotkey_all_monitors')}",
                lambda: self._in_thread(self.do_all_capture),
            ),
            pystray.MenuItem(
                f"Region {self._hotkey_label('hotkey_region')}",
                lambda: self._in_thread(self.do_region_capture),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _: f"Buffer: {self.buffer.frame_count()} frames",
                lambda: None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda _: (
                    f"{'Resume' if self.paused else 'Pause'} "
                    f"{self._hotkey_label('hotkey_pause')}"
                ),
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
        self._stop_clipboard_monitor()
        self._sight_module.stop()
        self._event_bus.close()
        self.activity_db.close()
        # Clean up tkinter root used by settings/dialogs
        from contextpulse_core.gui_theme import destroy_root
        destroy_root()
        if hasattr(self, "hotkey_listener") and self.hotkey_listener:
            self.hotkey_listener.stop()
        if hasattr(self, "_mutex") and self._mutex:
            get_platform_provider().release_single_instance_lock(self._mutex)
        if hasattr(self, "tray") and self.tray:
            self.tray.stop()

    # -- Run ---------------------------------------------------------------

    def run(self):
        # Single-instance guard via platform provider
        platform = get_platform_provider()
        self._mutex = platform.acquire_single_instance_lock("ContextPulseSight_SingleInstance")
        if self._mutex is None:
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
            cfg_get("auto_interval", _DEFAULTS["auto_interval"]),
            cfg_get("buffer_max_age", _DEFAULTS["buffer_max_age"]),
            cfg_get("change_threshold", _DEFAULTS["change_threshold"]),
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
        self._start_clipboard_monitor()

        # Started unconditionally. auto_interval == 0 is handled INSIDE the
        # loop; gating the thread on it here meant the watchdog never ran
        # either, so with auto_interval=0 a dead OCR worker, event detector or
        # hotkey listener was never restarted -- and the interval could not be
        # turned back on without a restart.
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
        # The chords themselves are logged once in __init__, where they are
        # parsed; repeating them here would be two lines saying one thing.
        logger.info("Tray icon ready.")
        self.tray.run()


def main():
    import sys as _sys

    # Handle --setup flag for MCP config generation
    if "--setup" in _sys.argv:
        from contextpulse_sight.setup import print_config, setup_all, setup_client

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
            print(
                "Usage: contextpulse-sight --setup "
                "{claude-code|cursor|gemini|claude-desktop|all|print}"
            )
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
