"""First-run welcome dialog for ContextPulse.

Shows hotkey reference and quick-start info.
Writes .first_run_complete marker to %APPDATA%/ContextPulse/.
"""

import logging
import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from contextpulse_core import gui_theme

logger = logging.getLogger(__name__)

APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "ContextPulse"
FIRST_RUN_MARKER = APPDATA_DIR / ".first_run_complete"


def is_first_run() -> bool:
    """Check if this is the first time running the app."""
    return not FIRST_RUN_MARKER.exists()


def mark_first_run_complete() -> None:
    """Mark first run as complete so the dialog doesn't show again."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    FIRST_RUN_MARKER.touch()


def show_welcome_dialog() -> None:
    """Show a welcome dialog for first-time users with hotkey reference."""
    dlg = gui_theme.create_dialog("ContextPulse — Welcome", width=500, height=400)

    frame = tk.Frame(dlg, bg=gui_theme.BG, padx=30, pady=20)
    frame.pack(fill="both", expand=True)

    # Title
    gui_theme.make_label(
        frame, "Welcome to ContextPulse",
        font=("Segoe UI", 18, "bold"), fg=gui_theme.ACCENT,
    ).pack(pady=(0, 5))

    gui_theme.make_label(
        frame, "Always-on screen capture for AI agents",
        font=("Segoe UI", 10), fg=gui_theme.TEXT_MUTED,
    ).pack(pady=(0, 15))

    # Hotkey reference table
    info_frame = tk.Frame(frame, bg=gui_theme.SURFACE, padx=15, pady=12)
    info_frame.pack(fill="x", pady=(0, 15))

    hotkeys = [
        ("Ctrl+Shift+S", "Quick capture (active monitor)"),
        ("Ctrl+Shift+A", "Capture all monitors"),
        ("Ctrl+Shift+Z", "Capture region around cursor"),
        ("Ctrl+Shift+P", "Pause / Resume capture"),
    ]
    for shortcut, desc in hotkeys:
        row = tk.Frame(info_frame, bg=gui_theme.SURFACE)
        row.pack(fill="x", pady=2)
        tk.Label(
            row, text=shortcut,
            font=("Consolas", 10, "bold"),
            fg=gui_theme.ACCENT, bg=gui_theme.SURFACE,
            width=18, anchor="w",
        ).pack(side="left")
        tk.Label(
            row, text=desc,
            font=("Segoe UI", 9),
            fg=gui_theme.TEXT, bg=gui_theme.SURFACE,
            anchor="w",
        ).pack(side="left")

    # Status info
    gui_theme.make_label(
        frame,
        "ContextPulse runs in your system tray. It captures your screen\n"
        "every 5 seconds and makes it available to any MCP-compatible AI agent.",
        font=("Segoe UI", 9), fg=gui_theme.TEXT_MUTED,
    ).pack(pady=(0, 5))

    gui_theme.make_label(
        frame,
        "Tray icon: green = active, yellow = paused",
        font=("Segoe UI", 9), fg=gui_theme.TEXT_MUTED,
    ).pack(pady=(0, 15))

    # Get Started button
    def on_start():
        mark_first_run_complete()
        dlg.destroy()

    ttk.Button(
        frame, text="Get Started", style="Accent.TButton",
        command=on_start,
    ).pack(pady=(5, 0))

    dlg.protocol("WM_DELETE_WINDOW", on_start)
    dlg.wait_window()
