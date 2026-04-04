# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Shared tkinter infrastructure for ContextPulse GUI dialogs.

Provides a singleton hidden Tk root (prevents multiple-Tk-instance bugs),
a dialog factory, and brand-consistent styles.
"""

import logging
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)

# ── Brand colors (from brand/colors.json) ────────────────────────────
BG = "#0D1117"
SURFACE = "#161B22"
SURFACE2 = "#1C2330"
TEXT = "#E6EDF3"
TEXT_MUTED = "#8B949E"
ACCENT = "#00E676"
ACCENT_HOVER = "#00CC66"
PRIMARY = "#0A6E8A"
PRIMARY_LIGHT = "#12B5E0"
BORDER = "#30363D"
ERROR = "#EF4444"
WARNING = "#F0B429"

# ── Singleton root ────────────────────────────────────────────────────
_root: tk.Tk | None = None


def _get_root() -> tk.Tk:
    """Return the singleton hidden Tk root, creating it on first call."""
    global _root
    if _root is None or not _root.winfo_exists():
        _root = tk.Tk()
        _root.withdraw()  # hidden — never shown
    return _root


def create_dialog(
    title: str,
    width: int = 520,
    height: int = 500,
) -> tk.Toplevel:
    """Create a centered, brand-styled Toplevel dialog.

    Returns a Toplevel that callers populate with widgets.
    The dialog is centered on screen and uses ContextPulse's dark theme.
    """
    root = _get_root()
    dlg = tk.Toplevel(root)
    dlg.title(title)
    dlg.configure(bg=BG)
    dlg.resizable(False, False)

    # Center on screen
    x = (dlg.winfo_screenwidth() - width) // 2
    y = (dlg.winfo_screenheight() - height) // 2
    dlg.geometry(f"{width}x{height}+{x}+{y}")

    # Apply shared ttk styles
    _apply_styles(dlg)

    return dlg


def _apply_styles(dlg: tk.Toplevel) -> None:
    """Configure ttk styles for ContextPulse brand."""
    style = ttk.Style(dlg)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Primary action button (green accent)
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=BG,
        font=("Segoe UI", 10, "bold"),
        padding=8,
    )
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])

    # Secondary button (surface)
    style.configure(
        "Secondary.TButton",
        background=SURFACE2,
        foreground=TEXT,
        font=("Segoe UI", 9),
        padding=8,
    )
    style.map("Secondary.TButton", background=[("active", BORDER)])

    # Disabled button
    style.configure(
        "Disabled.TButton",
        background=SURFACE,
        foreground=TEXT_MUTED,
        font=("Segoe UI", 9),
        padding=8,
    )


def make_label(
    parent: tk.Widget,
    text: str,
    *,
    font: tuple | None = None,
    fg: str = TEXT,
    anchor: str = "w",
    **kwargs,
) -> tk.Label:
    """Create a brand-styled label."""
    return tk.Label(
        parent,
        text=text,
        font=font or ("Segoe UI", 10),
        fg=fg,
        bg=BG,
        anchor=anchor,
        **kwargs,
    )


def make_entry(
    parent: tk.Widget,
    textvariable: tk.StringVar,
    *,
    show: str = "",
    **kwargs,
) -> tk.Entry:
    """Create a brand-styled text entry."""
    return tk.Entry(
        parent,
        textvariable=textvariable,
        font=("Consolas", 10),
        bg=SURFACE,
        fg=TEXT,
        insertbackground=ACCENT,
        relief="flat",
        show=show,
        **kwargs,
    )


def destroy_root() -> None:
    """Clean up the singleton root (call on app shutdown)."""
    global _root
    if _root is not None:
        try:
            _root.destroy()
        except Exception:
            pass
        _root = None
