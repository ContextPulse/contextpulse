"""Shared fixtures and mocks for core package tests.

Mocks platform-specific and heavy-GUI modules before any contextpulse_core
imports happen. This prevents tkinter, pystray, and ctypes.windll from
trying to initialize real hardware/display resources during testing.
"""

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock pystray (system tray) before any import can trigger it
# ---------------------------------------------------------------------------
_mock_pystray = MagicMock()
_mock_pystray.Icon = MagicMock
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Menu = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

# ---------------------------------------------------------------------------
# Mock tkinter at the sys.modules level so imports of settings/first_run/
# license_dialog don't trigger real Tk display connection attempts
# ---------------------------------------------------------------------------
_mock_tk = MagicMock()
_mock_tk.Tk = MagicMock
_mock_tk.Toplevel = MagicMock
_mock_tk.Frame = MagicMock
_mock_tk.Label = MagicMock
_mock_tk.Button = MagicMock
_mock_tk.Entry = MagicMock
_mock_tk.Text = MagicMock
_mock_tk.StringVar = MagicMock
_mock_tk.IntVar = MagicMock
_mock_tk.BooleanVar = MagicMock
_mock_tk.Scale = MagicMock
_mock_tk.OptionMenu = MagicMock
_mock_tk.Canvas = MagicMock
_mock_tk.Scrollbar = MagicMock
_mock_tk.END = "end"
_mock_tk.LEFT = "left"
_mock_tk.RIGHT = "right"
_mock_tk.BOTH = "both"
_mock_tk.X = "x"
_mock_tk.Y = "y"
_mock_tk.N = "n"
_mock_tk.S = "s"
_mock_tk.E = "e"
_mock_tk.W = "w"
sys.modules.setdefault("tkinter", _mock_tk)
sys.modules.setdefault("tkinter.ttk", MagicMock())
sys.modules.setdefault("tkinter.messagebox", MagicMock())
sys.modules.setdefault("tkinter.simpledialog", MagicMock())
sys.modules.setdefault("tkinter.font", MagicMock())

# ---------------------------------------------------------------------------
# Mock ctypes.windll for any non-Windows or headless environment
# ---------------------------------------------------------------------------
import ctypes
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()

# Ensure windll.kernel32 has what daemon.py needs
if not isinstance(getattr(ctypes, "windll", None), MagicMock):
    if not hasattr(ctypes.windll, "kernel32"):
        ctypes.windll.kernel32 = MagicMock()
