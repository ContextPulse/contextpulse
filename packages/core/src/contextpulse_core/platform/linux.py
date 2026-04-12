# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Linux stub implementation of PlatformProvider.

Contributors: implement these methods using the appropriate Linux APIs.
Key tools/libraries:
  - xclip / xsel / wl-clipboard for clipboard (X11/Wayland)
  - xdotool / wmctrl / Gio for window info
  - python-xlib or Wayland protocols for cursor position
  - D-Bus (org.freedesktop.login1) for session lock detection
"""

from contextpulse_core.platform.base import PlatformProvider


def _not_implemented(method_name: str, hint: str = "") -> NotImplementedError:
    msg = f"LinuxPlatformProvider.{method_name}() is not yet implemented."
    if hint:
        msg += f" Hint: {hint}"
    return NotImplementedError(msg)


class LinuxPlatformProvider(PlatformProvider):
    """Linux implementation — all methods raise NotImplementedError with hints."""

    def get_clipboard_sequence(self) -> int:
        raise _not_implemented(
            "get_clipboard_sequence",
            "X11 has no sequence number; poll clipboard content hash instead. "
            "For Wayland, use wl-paste --watch.",
        )

    def get_clipboard_text(self) -> str | None:
        raise _not_implemented(
            "get_clipboard_text",
            "Use subprocess: xclip -selection clipboard -o (X11) or "
            "wl-paste (Wayland). Or use python-xlib for native access.",
        )

    def get_foreground_window_title(self) -> str:
        raise _not_implemented(
            "get_foreground_window_title",
            "X11: use python-xlib to read _NET_ACTIVE_WINDOW + _NET_WM_NAME. "
            "Wayland: use compositor-specific protocols (sway/gnome).",
        )

    def get_foreground_process_name(self) -> str:
        raise _not_implemented(
            "get_foreground_process_name",
            "Get the PID from _NET_WM_PID (X11), then read /proc/<pid>/comm.",
        )

    def get_cursor_pos(self) -> tuple[int, int]:
        raise _not_implemented(
            "get_cursor_pos",
            "X11: use python-xlib display.screen().root.query_pointer(). "
            "Wayland: not directly available; use compositor IPC.",
        )

    def get_caret_position(self) -> tuple[int, int] | None:
        raise _not_implemented(
            "get_caret_position",
            "Use AT-SPI2 (python-atspi) to query the focused text widget's "
            "caret position via the Accessibility API.",
        )

    def create_session_monitor(self, on_lock: callable, on_unlock: callable):
        raise _not_implemented(
            "create_session_monitor",
            "Use D-Bus to listen for org.freedesktop.login1.Session "
            "Lock/Unlock signals.",
        )

    def acquire_single_instance_lock(self, name: str) -> object | None:
        raise _not_implemented(
            "acquire_single_instance_lock",
            "Use fcntl.flock() on a file in /tmp or /run/user/<uid>/.",
        )

    def release_single_instance_lock(self, handle: object) -> None:
        raise _not_implemented(
            "release_single_instance_lock",
            "Release the flock acquired above.",
        )
