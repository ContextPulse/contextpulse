"""Privacy controls: window title blocklist and session lock detection."""

import ctypes
import ctypes.wintypes
import logging
import threading

from contextpulse_sight.config import BLOCKLIST_PATTERNS

logger = logging.getLogger("contextpulse.sight.privacy")


# -- Window title blocklist ------------------------------------------------

def get_foreground_window_title() -> str:
    """Get the title of the currently active window."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_foreground_process_name() -> str:
    """Get the executable name of the foreground window's process."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    # Open process with QUERY_LIMITED_INFORMATION (0x1000)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        )
        if ok:
            # Return just the exe filename
            import os
            return os.path.basename(buf.value)
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def is_blocked() -> bool:
    """Return True if the foreground window matches any blocklist pattern."""
    if not BLOCKLIST_PATTERNS:
        return False
    title = get_foreground_window_title().lower()
    return any(p.lower() in title for p in BLOCKLIST_PATTERNS)


def is_title_blocked(title: str) -> bool:
    """Return True if a given window title matches any blocklist pattern.

    Used by MCP tools to filter stored history before returning results.
    """
    if not BLOCKLIST_PATTERNS or not title:
        return False
    title_lower = title.lower()
    return any(p.lower() in title_lower for p in BLOCKLIST_PATTERNS)


# -- Session lock/unlock detection ----------------------------------------

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
HWND_MESSAGE = ctypes.wintypes.HWND(-3)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HICON),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]


class SessionMonitor:
    """Monitors Windows session lock/unlock events via WTS notifications.

    Runs a hidden message-only window in a daemon thread to receive
    WM_WTSSESSION_CHANGE messages. Zero CPU when idle.
    """

    def __init__(self, on_lock: callable, on_unlock: callable):
        self.on_lock = on_lock
        self.on_unlock = on_unlock
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._hwnd = None

    def start(self):
        self._thread.start()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            if wparam == WTS_SESSION_LOCK:
                logger.info("Session locked")
                self.on_lock()
            elif wparam == WTS_SESSION_UNLOCK:
                logger.info("Session unlocked")
                self.on_unlock()
        DefWindowProcW = ctypes.windll.user32.DefWindowProcW
        DefWindowProcW.restype = ctypes.c_long
        DefWindowProcW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_uint,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
        ]
        return DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self._wndproc_ref = WNDPROC(self._wndproc)

        GetModuleHandleW = ctypes.windll.kernel32.GetModuleHandleW
        GetModuleHandleW.restype = ctypes.wintypes.HINSTANCE
        GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]

        RegisterClassW = ctypes.windll.user32.RegisterClassW
        RegisterClassW.restype = ctypes.wintypes.ATOM
        RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

        CreateWindowExW = ctypes.windll.user32.CreateWindowExW
        CreateWindowExW.restype = ctypes.wintypes.HWND
        CreateWindowExW.argtypes = [
            ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.wintypes.HWND, ctypes.wintypes.HMENU,
            ctypes.wintypes.HINSTANCE, ctypes.wintypes.LPVOID,
        ]

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = GetModuleHandleW(None)
        wc.lpszClassName = "ContextPulseSessionMonitor"

        atom = RegisterClassW(ctypes.byref(wc))
        if not atom:
            logger.error("Failed to register window class for session monitor")
            return

        self._hwnd = CreateWindowExW(
            0, wc.lpszClassName, "ContextPulse Session Monitor",
            0, 0, 0, 0, 0,
            HWND_MESSAGE, None, wc.hInstance, None,
        )
        if not self._hwnd:
            logger.error("Failed to create message window for session monitor")
            return

        wtsapi32 = ctypes.windll.wtsapi32
        wtsapi32.WTSRegisterSessionNotification(self._hwnd, NOTIFY_FOR_THIS_SESSION)

        logger.info("Session monitor active")

        msg = ctypes.wintypes.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

        wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
        ctypes.windll.user32.DestroyWindow(self._hwnd)
