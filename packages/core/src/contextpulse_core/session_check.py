# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Windows Session 0 detection.

Session 0 is the non-interactive Windows services session: no desktop, no
audio endpoint, no input queue. A process running there can still start
cleanly, open files, and bind ports -- it just cannot see a screen, hear a
microphone, or receive a keystroke. ContextPulse's entire value is
capturing those three things, so Session 0 is not a degraded mode, it is a
mode in which the product does nothing while looking alive.

Background (cp-daemon-session0-blind-capture): on 2026-09-15 a scheduled
health-check task's recovery path relaunched the whole daemon supervision
chain from within a task that itself ran in Session 0 (LogonType=S4U), and
it stayed there for 4 days. Every liveness signal the estate already had --
heartbeat file, process-alive, MCP port listening -- stayed green the whole
time, because none of them observe whether a capture actually produced
anything. dxcam's import-time DXFactory() call raised a COMError
(DXGI_ERROR_NOT_CURRENTLY_AVAILABLE) 10,524 times; mss would have "worked"
in the sense of returning bytes, except those bytes are a capture of a
desktop that Session 0 does not have.

This module answers exactly one question -- "what Windows session is THIS
process in?" -- via the Win32 API session id, not a heuristic (environment
variable, parent process name, command line) that something else could set
or fail to set. Any caller that needs to know whether it is safe to capture
should ask this, not infer it.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

logger = logging.getLogger(__name__)

# The well-known id of the non-interactive Windows services session.
SESSION_0 = 0


def get_windows_session_id(pid: int | None = None) -> int | None:
    """Return the Windows session id that ``pid`` (default: this process) is in.

    Uses ``ProcessIdToSessionId`` (kernel32) -- the same API Task Manager and
    Task Scheduler use to report session membership, so this answers the
    same question an operator looking at Task Manager would ask, not an
    approximation of it.

    Returns ``None`` on any non-Windows platform, or if the Win32 call
    itself fails (never raises). ``None`` means "could not determine" and
    is deliberately NOT the same value as ``0`` -- callers that must fail
    closed on an unknown session should treat ``None`` as unsafe themselves
    (see :mod:`contextpulse_core.daemon`'s startup guard); this function
    only reports what it observed.
    """
    if sys.platform != "win32":
        return None

    target_pid = pid if pid is not None else os.getpid()

    try:
        import ctypes.wintypes as wintypes

        session_id = wintypes.DWORD()
        kernel32 = ctypes.windll.kernel32
        ok = kernel32.ProcessIdToSessionId(
            wintypes.DWORD(target_pid), ctypes.pointer(session_id)
        )
        if not ok:
            logger.warning(
                "ProcessIdToSessionId failed for pid=%d (GetLastError=%d)",
                target_pid, kernel32.GetLastError(),
            )
            return None
        return int(session_id.value)
    except Exception:
        logger.warning(
            "Could not determine Windows session id for pid=%d", target_pid,
            exc_info=True,
        )
        return None


def is_session_0(pid: int | None = None) -> bool:
    """True only when the session id was determined AND is exactly 0.

    An undeterminable session (``None``) returns False here -- this helper
    answers "do we have positive proof this is Session 0", which is the
    right question for a diagnostic log line. A caller deciding whether it
    is SAFE to run (the daemon startup guard) wants the stricter "proof of
    safety, not merely absence of proof of danger" question instead, and
    should call :func:`get_windows_session_id` directly and fail closed on
    ``None`` too -- see ``daemon.py:_refuse_if_session_0``.
    """
    return get_windows_session_id(pid) == SESSION_0
