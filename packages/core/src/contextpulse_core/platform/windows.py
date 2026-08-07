# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Windows implementation of PlatformProvider using Win32 APIs via ctypes."""

import ctypes
import ctypes.wintypes
import logging
import os
import threading

from contextpulse_core.platform.base import PlatformProvider

logger = logging.getLogger("contextpulse.platform.windows")


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# -- Clipboard prototypes ---------------------------------------------------
# ctypes defaults an undeclared restype to c_int (32-bit signed). GetClipboardData
# returns an HGLOBAL and GlobalLock returns a raw memory pointer — both 64-bit on
# Win64. Leaving them undeclared truncates the high bits and sign-extends the
# result (a real 0x267C6C5ED40 came back as 0xFFFFFFFFA803D660), so wstring_at
# then faulted with an access violation. Declare the widths explicitly.
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_u32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
_u32.OpenClipboard.restype = ctypes.wintypes.BOOL
_u32.CloseClipboard.argtypes = []
_u32.CloseClipboard.restype = ctypes.wintypes.BOOL
_u32.IsClipboardFormatAvailable.argtypes = [ctypes.wintypes.UINT]
_u32.IsClipboardFormatAvailable.restype = ctypes.wintypes.BOOL
_u32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]
_u32.GetClipboardData.restype = ctypes.wintypes.HANDLE
_u32.GetClipboardSequenceNumber.argtypes = []
_u32.GetClipboardSequenceNumber.restype = ctypes.wintypes.DWORD
_k32.GlobalLock.argtypes = [ctypes.wintypes.HGLOBAL]
_k32.GlobalLock.restype = ctypes.wintypes.LPVOID
_k32.GlobalUnlock.argtypes = [ctypes.wintypes.HGLOBAL]
_k32.GlobalUnlock.restype = ctypes.wintypes.BOOL


class WindowsPlatformProvider(PlatformProvider):
    """Win32 implementation of all platform-specific operations."""

    # -- Clipboard ---------------------------------------------------------

    def get_clipboard_sequence(self) -> int:
        """Return the Win32 clipboard sequence number."""
        try:
            return _u32.GetClipboardSequenceNumber()
        except Exception:
            logger.warning("GetClipboardSequenceNumber failed", exc_info=True)
            return 0

    def get_clipboard_text(self) -> str | None:
        """Read text from the Windows clipboard using Win32 API.

        Returns None when the clipboard holds no text (a normal, quiet case).
        A genuine API failure is logged rather than swallowed — this read is on
        a 1s poll loop, so the log is rate-limited to one warning per distinct
        error to surface breakage without flooding the daemon log.
        """
        CF_UNICODETEXT = 13
        try:
            if not _u32.OpenClipboard(None):
                return None
            try:
                if not _u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return None
                handle = _u32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return None
                ptr = _k32.GlobalLock(handle)
                if not ptr:
                    return None
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    _k32.GlobalUnlock(handle)
            finally:
                _u32.CloseClipboard()
        except Exception as exc:
            self._warn_once("clipboard_read", exc)
            return None

    _warned_errors: set = set()

    def _warn_once(self, key: str, exc: BaseException) -> None:
        """Log a warning the first time each distinct error signature appears."""
        sig = (key, type(exc).__name__, str(exc)[:200])
        if sig in self._warned_errors:
            return
        self._warned_errors.add(sig)
        logger.warning("Clipboard read failed (%s): %r", key, exc, exc_info=True)

    # -- Window info -------------------------------------------------------

    def get_foreground_window_title(self) -> str:
        """Get the title of the currently active window using Win32 API."""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def get_foreground_process_name(self) -> str:
        """Get the executable name of the foreground window's process."""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
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
                return os.path.basename(buf.value)
            return ""
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    # -- Cursor / pointer --------------------------------------------------

    def get_cursor_pos(self) -> tuple[int, int]:
        """Get current cursor position using Win32 GetCursorPos."""
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    # -- Caret (text cursor) -----------------------------------------------

    def get_caret_position(self) -> tuple[int, int] | None:
        """Get the text caret position using Win32 GetGUIThreadInfo."""
        try:
            import ctypes.wintypes as wt

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wt.DWORD),
                    ("flags", wt.DWORD),
                    ("hwndActive", wt.HWND),
                    ("hwndFocus", wt.HWND),
                    ("hwndCapture", wt.HWND),
                    ("hwndMenuOwner", wt.HWND),
                    ("hwndMoveSize", wt.HWND),
                    ("hwndCaret", wt.HWND),
                    ("rcCaret", wt.RECT),
                ]

            gui = GUITHREADINFO()
            gui.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(gui)):
                return None
            if not gui.hwndCaret:
                return None

            point = wt.POINT(gui.rcCaret.left, gui.rcCaret.top)
            ctypes.windll.user32.ClientToScreen(gui.hwndCaret, ctypes.byref(point))
            return (point.x, point.y)
        except Exception:
            return None

    # -- Session lock detection --------------------------------------------

    def create_session_monitor(self, on_lock: callable, on_unlock: callable):
        """Create a Windows session lock/unlock monitor using WTS notifications."""
        return _WindowsSessionMonitor(on_lock=on_lock, on_unlock=on_unlock)

    # -- Single-instance guard ---------------------------------------------

    def acquire_single_instance_lock(self, name: str) -> object | None:
        """Acquire a Windows named mutex for single-instance enforcement.

        Uses SetLastError(0) before CreateMutexW to prevent stale error codes
        from masking ERROR_ALREADY_EXISTS. Closes the handle on duplicate to
        prevent zombie processes holding dangling mutex handles.

        Retries up to 3 times with 2s gaps to handle the race condition where
        a crashed daemon's mutex hasn't been released by the OS yet.
        """
        import time
        ERROR_ALREADY_EXISTS = 183
        for attempt in range(3):
            ctypes.windll.kernel32.SetLastError(0)
            mutex = ctypes.windll.kernel32.CreateMutexW(None, True, name)
            last_error = ctypes.windll.kernel32.GetLastError()
            if last_error == ERROR_ALREADY_EXISTS:
                # Close the handle — otherwise this process holds a dangling
                # reference that keeps the mutex alive even if the owner dies.
                if mutex:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                if attempt < 2:
                    logger.info(
                        "Mutex held by another process (attempt %d/3), retrying in 2s...",
                        attempt + 1,
                    )
                    time.sleep(2)
                    continue
                return None
            if not mutex:
                return None
            return mutex
        return None

    def find_contextpulse_processes(self, exclude_pid: int | None = None) -> list[int]:
        """Find PIDs of running ContextPulse daemon processes.

        Scans for pythonw.exe processes whose command line contains
        'contextpulse'. Optionally excludes a specific PID (e.g., self).
        """
        import subprocess
        pids: list[int] = []
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" "
                 "| Where-Object { $_.CommandLine -like '*contextpulse*' } "
                 "| Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid != exclude_pid:
                        pids.append(pid)
        except Exception:
            pass
        return pids

    def kill_process(self, pid: int) -> bool:
        """Kill a process by PID. Returns True on success."""
        import subprocess
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    def release_single_instance_lock(self, handle: object) -> None:
        """Release a Windows named mutex."""
        if handle:
            ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)


# -- Session monitor implementation ----------------------------------------

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


class _WNDCLASSW(ctypes.Structure):
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


class _WindowsSessionMonitor:
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
        RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]

        CreateWindowExW = ctypes.windll.user32.CreateWindowExW
        CreateWindowExW.restype = ctypes.wintypes.HWND
        CreateWindowExW.argtypes = [
            ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.wintypes.HWND, ctypes.wintypes.HMENU,
            ctypes.wintypes.HINSTANCE, ctypes.wintypes.LPVOID,
        ]

        wc = _WNDCLASSW()
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
