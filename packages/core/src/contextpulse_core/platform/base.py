"""Abstract base class for platform-specific operations.

Each method documents what it should do so contributors can implement
Mac/Linux support by subclassing PlatformProvider.
"""

from abc import ABC, abstractmethod


class PlatformProvider(ABC):
    """Abstract interface for OS-specific operations used by ContextPulse.

    Implementations exist for Windows (fully functional), macOS (stub),
    and Linux (stub). To add support for a new platform, subclass this
    and implement every abstract method.
    """

    # -- Clipboard ---------------------------------------------------------

    @abstractmethod
    def get_clipboard_sequence(self) -> int:
        """Return a sequence number that changes each time the clipboard is updated.

        Used as a cheap check to avoid reading clipboard contents unnecessarily.
        If the platform has no equivalent, return a monotonically increasing
        counter on each call (or 0 to force a content read every time).
        """

    @abstractmethod
    def get_clipboard_text(self) -> str | None:
        """Read the current clipboard text content.

        Returns None if the clipboard is empty, inaccessible, or does not
        contain text. Must not raise exceptions.
        """

    # -- Window info -------------------------------------------------------

    @abstractmethod
    def get_foreground_window_title(self) -> str:
        """Return the title of the currently focused/foreground window.

        Returns empty string if no window is focused or the title cannot
        be determined.
        """

    @abstractmethod
    def get_foreground_process_name(self) -> str:
        """Return the executable filename (e.g. 'code.exe') of the foreground window's process.

        Returns empty string if the process cannot be determined.
        """

    # -- Cursor / pointer --------------------------------------------------

    @abstractmethod
    def get_cursor_pos(self) -> tuple[int, int]:
        """Return the current mouse cursor position in screen coordinates as (x, y)."""

    # -- Caret (text cursor) -----------------------------------------------

    @abstractmethod
    def get_caret_position(self) -> tuple[int, int] | None:
        """Return the screen position of the text caret (blinking cursor).

        Returns (x, y) in screen coordinates, or None if the caret position
        cannot be determined (e.g. no focused text field).
        """

    # -- Session lock detection --------------------------------------------

    @abstractmethod
    def create_session_monitor(self, on_lock: callable, on_unlock: callable):
        """Create and return a session lock/unlock monitor.

        The returned object must have a start() method that begins monitoring
        in a daemon thread. When the user locks the screen, call on_lock().
        When they unlock, call on_unlock().

        The returned object should be an instance with a start() method.
        """

    # -- Single-instance guard ---------------------------------------------

    @abstractmethod
    def acquire_single_instance_lock(self, name: str) -> object | None:
        """Attempt to acquire a system-wide named lock for single-instance enforcement.

        Returns an opaque lock handle on success, or None if another instance
        already holds the lock. The handle is passed to release_single_instance_lock().
        """

    @abstractmethod
    def release_single_instance_lock(self, handle: object) -> None:
        """Release a previously acquired single-instance lock."""
