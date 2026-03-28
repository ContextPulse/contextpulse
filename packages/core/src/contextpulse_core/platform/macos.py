"""macOS stub implementation of PlatformProvider.

Contributors: implement these methods using PyObjC / AppKit / Quartz.
Key frameworks:
  - AppKit (NSWorkspace, NSPasteboard) for clipboard and window info
  - Quartz (CGEvent) for cursor position
  - CoreGraphics for screen capture coordination
  - DistributedNotificationCenter for session lock
"""

from contextpulse_core.platform.base import PlatformProvider


def _not_implemented(method_name: str, hint: str = "") -> NotImplementedError:
    msg = f"MacPlatformProvider.{method_name}() is not yet implemented."
    if hint:
        msg += f" Hint: {hint}"
    return NotImplementedError(msg)


class MacPlatformProvider(PlatformProvider):
    """macOS implementation — all methods raise NotImplementedError with hints."""

    def get_clipboard_sequence(self) -> int:
        raise _not_implemented(
            "get_clipboard_sequence",
            "Use NSPasteboard.generalPasteboard().changeCount",
        )

    def get_clipboard_text(self) -> str | None:
        raise _not_implemented(
            "get_clipboard_text",
            "Use NSPasteboard.generalPasteboard().stringForType_(NSStringPboardType)",
        )

    def get_foreground_window_title(self) -> str:
        raise _not_implemented(
            "get_foreground_window_title",
            "Use NSWorkspace.sharedWorkspace().frontmostApplication and "
            "CGWindowListCopyWindowInfo for the window title",
        )

    def get_foreground_process_name(self) -> str:
        raise _not_implemented(
            "get_foreground_process_name",
            "Use NSWorkspace.sharedWorkspace().frontmostApplication.localizedName()",
        )

    def get_cursor_pos(self) -> tuple[int, int]:
        raise _not_implemented(
            "get_cursor_pos",
            "Use Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))",
        )

    def get_caret_position(self) -> tuple[int, int] | None:
        raise _not_implemented(
            "get_caret_position",
            "Use the Accessibility API (AXUIElement) to query the focused "
            "text field's AXSelectedTextRange and AXBoundsForRange",
        )

    def create_session_monitor(self, on_lock: callable, on_unlock: callable):
        raise _not_implemented(
            "create_session_monitor",
            "Listen for com.apple.screenIsLocked and com.apple.screenIsUnlocked "
            "via NSDistributedNotificationCenter",
        )

    def acquire_single_instance_lock(self, name: str) -> object | None:
        raise _not_implemented(
            "acquire_single_instance_lock",
            "Use fcntl.flock() on a file in /tmp, or NSDistributedLock",
        )

    def release_single_instance_lock(self, handle: object) -> None:
        raise _not_implemented(
            "release_single_instance_lock",
            "Release the flock or NSDistributedLock acquired above",
        )
