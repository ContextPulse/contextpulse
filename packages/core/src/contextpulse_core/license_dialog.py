# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""License nag dialog for ContextPulse Memory features.

Shows tier info, days remaining, purchase link.
Key messaging: "Sight remains free" — nag only gates Memory/Agent.
"""

import logging
import tkinter as tk
from tkinter import ttk

from contextpulse_core import gui_theme
from contextpulse_core.license import (
    get_license_email,
    get_trial_days_remaining,
    save_license,
)

logger = logging.getLogger(__name__)


def show_nag_dialog() -> bool:
    """Show the license nag dialog. Returns True if user continues."""
    result = {"continue": False}

    dlg = gui_theme.create_dialog("ContextPulse — License", width=500, height=420)

    frame = tk.Frame(dlg, bg=gui_theme.BG, padx=30, pady=20)
    frame.pack(fill="both", expand=True)

    # Title
    gui_theme.make_label(
        frame, "ContextPulse",
        font=("Segoe UI", 22, "bold"), fg=gui_theme.ACCENT,
    ).pack(pady=(0, 3))

    # Trial status
    days_left = get_trial_days_remaining()
    trial_expired = days_left <= 0

    if trial_expired:
        trial_msg = "Your 30-day Pro trial has expired. Upgrade to keep semantic search and cross-modal tools."
        trial_color = gui_theme.ERROR
    else:
        trial_msg = f"Pro trial: {days_left} day{'s' if days_left != 1 else ''} remaining — memory store/recall/list/forget stay free forever."
        trial_color = gui_theme.TEXT_MUTED

    gui_theme.make_label(
        frame, trial_msg,
        font=("Segoe UI", 10), fg=trial_color,
    ).pack(pady=(5, 3))

    # Free forever reassurance
    gui_theme.make_label(
        frame,
        "Sight (screen capture) and basic memory (store/recall/list/forget) are free forever.",
        font=("Segoe UI", 9), fg=gui_theme.TEXT_MUTED,
    ).pack(pady=(0, 15))

    # License key entry
    gui_theme.make_label(
        frame, "Enter your license key:",
        font=("Segoe UI", 10), fg=gui_theme.TEXT_MUTED,
    ).pack(fill="x", pady=(0, 5))

    key_entry = tk.Text(
        frame, height=3, font=("Consolas", 10),
        bg=gui_theme.SURFACE, fg=gui_theme.TEXT,
        insertbackground=gui_theme.ACCENT,
        relief="flat", padx=8, pady=6, wrap="char",
    )
    key_entry.pack(fill="x", pady=(0, 10))

    status_label = gui_theme.make_label(
        frame, "", font=("Segoe UI", 9), fg=gui_theme.TEXT_MUTED,
    )
    status_label.pack(pady=(0, 10))

    btn_frame = tk.Frame(frame, bg=gui_theme.BG)
    btn_frame.pack(fill="x", pady=(0, 5))

    def activate():
        key_text = key_entry.get("1.0", "end").strip()
        if not key_text:
            status_label.config(text="Please paste your license key.", fg=gui_theme.ERROR)
            return

        payload = save_license(key_text)
        if payload:
            email = payload.get("email", "")
            tier = payload.get("tier", "unknown")
            status_label.config(
                text=f"Licensed to {email} ({tier})",
                fg=gui_theme.ACCENT,
            )
            result["continue"] = True
            dlg.after(800, dlg.destroy)
        else:
            status_label.config(
                text="Invalid license key. Please check and try again.",
                fg=gui_theme.ERROR,
            )

    def use_trial():
        result["continue"] = True
        dlg.destroy()

    ttk.Button(
        btn_frame, text="Activate License", style="Accent.TButton",
        command=activate,
    ).pack(side="left", padx=(0, 10))

    trial_btn = ttk.Button(
        btn_frame, text="Continue Trial", style="Secondary.TButton",
        command=use_trial,
    )
    trial_btn.pack(side="left")
    if trial_expired:
        trial_btn.state(["disabled"])

    # Purchase link (clickable)
    purchase_label = gui_theme.make_label(
        frame,
        "Upgrade at contextpulse.ai/pricing  (Pro $49/yr · Lifetime $249)",
        font=("Segoe UI", 9, "underline"), fg=gui_theme.ACCENT,
    )
    purchase_label.pack(pady=(10, 0))
    purchase_label.bind(
        "<Button-1>",
        lambda _: __import__("webbrowser").open("https://contextpulse.ai/pricing"),
    )
    purchase_label.config(cursor="hand2")

    def on_close():
        if trial_expired:
            dlg.destroy()  # result stays False — Memory features blocked
        else:
            use_trial()

    dlg.protocol("WM_DELETE_WINDOW", on_close)
    dlg.wait_window()
    return result["continue"]


def show_licensed_badge() -> str | None:
    """Return the licensed email for display, or None if unlicensed."""
    return get_license_email()
