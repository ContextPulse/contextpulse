"""Settings panel for ContextPulse.

Sections:
  - Capture: interval slider, storage mode dropdown
  - Hotkeys: 4 configurable hotkeys
  - Privacy: blocklist patterns, always-both apps
  - License: status badge, tier, email, "Enter Key" button
Saves to %APPDATA%/ContextPulse/config.json via config module.
"""

import logging
import tkinter as tk
from tkinter import ttk, messagebox

from contextpulse_core import gui_theme
from contextpulse_core.config import load_config, save_config
from contextpulse_core.license import (
    get_license_email,
    get_license_tier,
    get_trial_days_remaining,
    is_licensed,
    is_trial_expired,
)

logger = logging.getLogger(__name__)

_settings_open = False


def show_settings() -> None:
    """Show the settings dialog. Prevents duplicate windows."""
    global _settings_open
    if _settings_open:
        return
    _settings_open = True
    try:
        _build_and_run()
    finally:
        _settings_open = False


def _section_header(parent: tk.Frame, text: str) -> None:
    """Add a section header label."""
    gui_theme.make_label(
        parent, text,
        font=("Segoe UI", 12, "bold"), fg=gui_theme.ACCENT,
    ).pack(anchor="w", pady=(15, 5))


def _field_row(
    parent: tk.Frame,
    label_text: str,
    var: tk.Variable,
    *,
    width: int = 0,
    entry_type: str = "entry",
    values: list[str] | None = None,
) -> tk.Widget:
    """Add a label + input row. Returns the input widget."""
    row = tk.Frame(parent, bg=gui_theme.BG)
    row.pack(fill="x", pady=2)

    tk.Label(
        row, text=label_text,
        font=("Segoe UI", 10), fg=gui_theme.TEXT_MUTED, bg=gui_theme.BG,
        width=22, anchor="w",
    ).pack(side="left")

    if entry_type == "combo" and values:
        widget = ttk.Combobox(
            row, textvariable=var, values=values,
            state="readonly", width=width or 12,
        )
        widget.pack(side="left")
    elif entry_type == "spin":
        widget = tk.Spinbox(
            row, textvariable=var,
            from_=0, to=300, increment=1,
            font=("Consolas", 10), width=width or 6,
            bg=gui_theme.SURFACE, fg=gui_theme.TEXT,
            insertbackground=gui_theme.ACCENT, relief="flat",
        )
        widget.pack(side="left")
    else:
        widget = gui_theme.make_entry(row, var)
        widget.pack(side="left", fill="x", expand=True, ipady=4)

    return widget


def _build_and_run() -> None:
    cfg = load_config()

    dlg = gui_theme.create_dialog("ContextPulse — Settings", width=560, height=620)

    # Scrollable canvas for content
    canvas = tk.Canvas(dlg, bg=gui_theme.BG, highlightthickness=0)
    scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
    frame = tk.Frame(canvas, bg=gui_theme.BG, padx=25, pady=15)

    frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Enable mousewheel scrolling
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    root = gui_theme._get_root()

    # ── Title ─────────────────────────────────────────────────────
    gui_theme.make_label(
        frame, "Settings",
        font=("Segoe UI", 18, "bold"), fg=gui_theme.ACCENT,
    ).pack(anchor="w", pady=(0, 5))

    # ── Capture Section ───────────────────────────────────────────
    _section_header(frame, "Capture")

    interval_var = tk.IntVar(master=root, value=cfg.get("auto_interval", 5))
    _field_row(frame, "Auto-capture interval (s):", interval_var, entry_type="spin")

    storage_var = tk.StringVar(master=root, value=cfg.get("storage_mode", "smart"))
    _field_row(
        frame, "Storage mode:", storage_var,
        entry_type="combo", values=["smart", "visual", "both", "text"],
    )

    quality_var = tk.IntVar(master=root, value=cfg.get("jpeg_quality", 75))
    _field_row(frame, "JPEG quality (1-100):", quality_var, entry_type="spin")

    buffer_var = tk.IntVar(master=root, value=cfg.get("buffer_max_age", 1800))
    _field_row(frame, "Buffer max age (seconds):", buffer_var, entry_type="spin")

    # ── Hotkeys Section ───────────────────────────────────────────
    _section_header(frame, "Hotkeys")

    hk_capture_var = tk.StringVar(master=root, value=cfg.get("hotkey_capture", "ctrl+shift+s"))
    _field_row(frame, "Quick capture:", hk_capture_var)

    hk_all_var = tk.StringVar(master=root, value=cfg.get("hotkey_all_monitors", "ctrl+shift+a"))
    _field_row(frame, "All monitors:", hk_all_var)

    hk_region_var = tk.StringVar(master=root, value=cfg.get("hotkey_region", "ctrl+shift+z"))
    _field_row(frame, "Region capture:", hk_region_var)

    hk_pause_var = tk.StringVar(master=root, value=cfg.get("hotkey_pause", "ctrl+shift+p"))
    _field_row(frame, "Pause/Resume:", hk_pause_var)

    gui_theme.make_label(
        frame, "Hotkey changes take effect after restart.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    # ── Privacy Section ───────────────────────────────────────────
    _section_header(frame, "Privacy")

    blocklist_str = ", ".join(cfg.get("blocklist_patterns", []))
    blocklist_var = tk.StringVar(master=root, value=blocklist_str)
    _field_row(frame, "Blocklist (comma-sep):", blocklist_var)

    always_both_str = ", ".join(cfg.get("always_both_apps", []))
    always_both_var = tk.StringVar(master=root, value=always_both_str)
    _field_row(frame, "Always keep image+text:", always_both_var)

    gui_theme.make_label(
        frame, "Blocklist: window titles containing these strings are never captured.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    redact_var = tk.StringVar(master=root, value="1" if cfg.get("redact_ocr_text", True) else "0")
    tk.Checkbutton(
        frame, text="  Redact sensitive text from OCR (API keys, passwords, tokens)",
        variable=redact_var, onvalue="1", offvalue="0",
        font=("Segoe UI", 10),
        fg=gui_theme.TEXT, bg=gui_theme.BG, selectcolor=gui_theme.BG,
        activebackground=gui_theme.BG, activeforeground=gui_theme.TEXT,
        highlightthickness=0, bd=1,
    ).pack(anchor="w", pady=(8, 0))

    # ── License Section ───────────────────────────────────────────
    _section_header(frame, "License")

    email = get_license_email()
    tier = get_license_tier()
    licensed = is_licensed()

    if licensed and email:
        license_text = f"Licensed to {email} ({tier})"
        license_color = gui_theme.ACCENT
    elif not is_trial_expired():
        days = get_trial_days_remaining()
        license_text = f"Memory trial: {days} day{'s' if days != 1 else ''} remaining"
        license_color = gui_theme.PRIMARY_LIGHT
    else:
        license_text = "Memory trial expired — enter a license key"
        license_color = gui_theme.ERROR

    gui_theme.make_label(
        frame, license_text,
        font=("Segoe UI", 10), fg=license_color,
    ).pack(anchor="w", pady=(0, 5))

    gui_theme.make_label(
        frame, "Sight (screen capture) is always free. License is for Memory/Agent features.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(0, 5))

    # Enter Key button
    def open_license_dialog():
        from contextpulse_core.license_dialog import show_nag_dialog
        show_nag_dialog()
        # Refresh license display
        dlg.destroy()

    ttk.Button(
        frame, text="Enter License Key", style="Secondary.TButton",
        command=open_license_dialog,
    ).pack(anchor="w", pady=(5, 0))

    # ── Save & Close ──────────────────────────────────────────────
    # Capture startup values for change detection
    startup_hotkeys = (
        cfg.get("hotkey_capture", ""),
        cfg.get("hotkey_all_monitors", ""),
        cfg.get("hotkey_region", ""),
        cfg.get("hotkey_pause", ""),
    )

    def save_and_close():
        new_cfg = dict(cfg)  # preserve any unknown keys
        new_cfg.update({
            "auto_interval": max(0, interval_var.get()),
            "storage_mode": storage_var.get(),
            "jpeg_quality": max(1, min(100, quality_var.get())),
            "buffer_max_age": max(0, buffer_var.get()),
            "hotkey_capture": hk_capture_var.get().strip().lower() or "ctrl+shift+s",
            "hotkey_all_monitors": hk_all_var.get().strip().lower() or "ctrl+shift+a",
            "hotkey_region": hk_region_var.get().strip().lower() or "ctrl+shift+z",
            "hotkey_pause": hk_pause_var.get().strip().lower() or "ctrl+shift+p",
            "blocklist_patterns": [p.strip() for p in blocklist_var.get().split(",") if p.strip()],
            "always_both_apps": [p.strip() for p in always_both_var.get().split(",") if p.strip()],
            "redact_ocr_text": redact_var.get() == "1",
        })
        save_config(new_cfg)
        logger.info("Settings saved")

        new_hotkeys = (
            new_cfg["hotkey_capture"],
            new_cfg["hotkey_all_monitors"],
            new_cfg["hotkey_region"],
            new_cfg["hotkey_pause"],
        )
        if new_hotkeys != startup_hotkeys:
            messagebox.showinfo(
                "ContextPulse",
                "Hotkey changes will take effect after restarting ContextPulse.",
            )

        dlg.destroy()

    # Bottom button bar
    btn_frame = tk.Frame(frame, bg=gui_theme.BG)
    btn_frame.pack(fill="x", pady=(20, 5))

    ttk.Button(
        btn_frame, text="Save & Close", style="Accent.TButton",
        command=save_and_close,
    ).pack(side="right")

    ttk.Button(
        btn_frame, text="Cancel", style="Secondary.TButton",
        command=dlg.destroy,
    ).pack(side="right", padx=(0, 10))

    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    # Unbind mousewheel on close to prevent errors
    def _on_close():
        canvas.unbind_all("<MouseWheel>")
        dlg.destroy()

    dlg.protocol("WM_DELETE_WINDOW", _on_close)
    dlg.wait_window()
