"""Input listeners — thin pynput wrappers for keyboard and mouse capture."""

import logging
import threading
import time
from typing import Any, Callable

from contextpulse_core.platform import get_platform_provider
from pynput import keyboard as kb
from pynput import mouse as ms

logger = logging.getLogger(__name__)


def _get_clipboard_text() -> str:
    """Read current clipboard text. Returns empty string on failure."""
    try:
        return get_platform_provider().get_clipboard_text() or ""
    except Exception:
        return ""


def _get_foreground_info() -> tuple[str, str]:
    """Get current foreground app name and window title."""
    try:
        platform = get_platform_provider()
        app_name = platform.get_foreground_process_name()
        title = platform.get_foreground_window_title()
        return (app_name, title)
    except Exception:
        return ("", "")


class KeyboardListener:
    """Wraps pynput keyboard listener. Feeds events to BurstTracker and CorrectionDetector."""

    def __init__(
        self,
        on_char: Callable[[str | None, bool, bool], None] | None = None,
        on_paste: Callable[[str], None] | None = None,
        on_key_event: Callable[[bool, bool], None] | None = None,
    ) -> None:
        self._on_char = on_char          # (key_char, is_backspace, is_selection)
        self._on_paste = on_paste        # (clipboard_text)
        self._on_key_event = on_key_event  # (is_backspace, is_selection)
        self._listener: kb.Listener | None = None
        self._pressed_keys: set = set()

    def start(self) -> None:
        self._listener = kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("KeyboardListener started")

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        logger.info("KeyboardListener stopped")

    def _on_press(self, key) -> None:
        self._pressed_keys.add(key)

        # Track generic modifier variants
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self._pressed_keys.add(kb.Key.ctrl_l)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self._pressed_keys.add(kb.Key.shift_l)

        # Detect Ctrl+V (paste)
        if (key == kb.KeyCode.from_char('v') and
                kb.Key.ctrl_l in self._pressed_keys):
            if self._on_paste:
                # Small delay to let clipboard update
                threading.Timer(0.1, self._handle_paste).start()
            return

        # Detect backspace
        is_backspace = key in (kb.Key.backspace, kb.Key.delete)

        # Detect selection (Shift+arrow)
        is_selection = (
            kb.Key.shift_l in self._pressed_keys and
            key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                    kb.Key.home, kb.Key.end)
        )

        # Extract character
        key_char = None
        if hasattr(key, 'char') and key.char:
            key_char = key.char

        if self._on_char:
            self._on_char(key_char, is_backspace, is_selection)

        if self._on_key_event:
            self._on_key_event(is_backspace, is_selection)

    def _on_release(self, key) -> None:
        self._pressed_keys.discard(key)
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self._pressed_keys.discard(kb.Key.ctrl_l)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self._pressed_keys.discard(kb.Key.shift_l)

    def _handle_paste(self) -> None:
        """Read clipboard after paste and notify handler."""
        text = _get_clipboard_text()
        if text and self._on_paste:
            self._on_paste(text)


class MouseListener:
    """Wraps pynput mouse listener. Emits click, scroll, and drag events."""

    def __init__(
        self,
        on_click: Callable[[dict[str, Any]], None] | None = None,
        on_scroll: Callable[[dict[str, Any]], None] | None = None,
        on_drag: Callable[[dict[str, Any]], None] | None = None,
        on_window_change: Callable[[], None] | None = None,
        debounce_seconds: float = 0.1,
    ) -> None:
        self._on_click = on_click
        self._on_scroll = on_scroll
        self._on_drag = on_drag
        self._on_window_change = on_window_change
        self._debounce = debounce_seconds
        self._listener: ms.Listener | None = None

        self._last_event_time: float = 0.0
        self._drag_start: tuple[int, int] | None = None
        self._drag_start_time: float = 0.0
        self._last_window_title: str = ""

    def start(self) -> None:
        self._listener = ms.Listener(
            on_click=self._handle_click,
            on_scroll=self._handle_scroll,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("MouseListener started")

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _debounce_check(self) -> bool:
        """Return True if enough time has passed since last event."""
        now = time.time()
        if now - self._last_event_time < self._debounce:
            return False
        self._last_event_time = now
        return True

    def _check_window_change(self) -> tuple[str, str]:
        """Check if foreground window changed. Returns (app_name, title)."""
        app_name, title = _get_foreground_info()
        if title != self._last_window_title:
            self._last_window_title = title
            if self._on_window_change:
                self._on_window_change()
        return app_name, title

    def _handle_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not pressed:  # Only fire on press, not release
            # Check for drag end
            if self._drag_start:
                sx, sy = self._drag_start
                dist = ((x - sx) ** 2 + (y - sy) ** 2) ** 0.5
                if dist > 20 and self._on_drag:  # Minimum drag distance
                    app_name, title = _get_foreground_info()
                    self._on_drag({
                        "start_x": sx, "start_y": sy,
                        "end_x": x, "end_y": y,
                        "duration_ms": int((time.time() - self._drag_start_time) * 1000),
                        "app_name": app_name,
                        "window_title": title,
                    })
                self._drag_start = None
            return

        if not self._debounce_check():
            return

        self._drag_start = (x, y)
        self._drag_start_time = time.time()

        app_name, title = self._check_window_change()

        if self._on_click:
            button_name = "left"
            if hasattr(button, 'name'):
                button_name = button.name
            self._on_click({
                "x": x, "y": y,
                "button": button_name,
                "click_type": "single",
                "app_name": app_name,
                "window_title": title,
            })

    def _handle_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._debounce_check():
            return

        app_name, title = _get_foreground_info()

        if self._on_scroll:
            self._on_scroll({
                "x": x, "y": y,
                "dx": dx, "dy": dy,
                "app_name": app_name,
                "window_title": title,
            })
