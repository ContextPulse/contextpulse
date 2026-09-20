# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Settings panel for ContextPulse.

Sections:
  - Capture: interval slider, storage mode dropdown
  - Hotkeys: 4 configurable hotkeys
  - Privacy: blocklist patterns, always-both apps
  - License: status badge, tier, email, "Enter Key" button
  - MCP Access: the bearer token clients need, show/copy/regenerate
Saves to %APPDATA%/ContextPulse/config.json via config module.
"""

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from contextpulse_core import clipboard_lock, gui_theme, mcp_auth
from contextpulse_core.config import _CLAMPS, _DEFAULTS, load_config, save_config
from contextpulse_core.license import (
    get_license_email,
    get_license_tier,
    get_trial_days_remaining,
    is_licensed,
    is_trial_expired,
)

logger = logging.getLogger(__name__)

_settings_open = False

# Ceiling for a spinner whose config key has no upper clamp. 86400 = 24h;
# every spin field here except jpeg_quality is seconds-valued, and
# jpeg_quality carries its own (1, 100) in _CLAMPS. See _spin_range().
_UNBOUNDED_SPIN_CEILING = 86400

# ── Keys this dialog can change that do NOT take effect until restart ──
#
# Every other control here is read at the point of use, so saving it changes
# behaviour immediately. These nine are cached by whichever module owns them:
# the four sight hotkeys are parsed once in ContextPulseSightApp.__init__, the
# three voice keys once in VoiceModule.__init__ (the model is loaded from
# disk), and the two touch keys once in TouchModule.__init__ when it builds
# its listeners.
#
# The notice used to be keyed on a seven-element tuple named `startup_hotkeys`
# and its text said "Hotkey changes will take effect after restarting" -- so
# changing the Whisper model or a touch timing silently did nothing and said
# nothing, and changing the model said "hotkey". The names and the text are
# now derived from this one tuple.
#
# NOT here on purpose: touch_min_burst_chars and touch_mouse_debounce are
# equally startup-bound but this dialog exposes no control for them, so they
# can never be the reason a notice fires.
_RESTART_KEYS: tuple[str, ...] = (
    "hotkey_capture",
    "hotkey_all_monitors",
    "hotkey_region",
    "hotkey_pause",
    "voice_hotkey",
    "voice_fix_hotkey",
    "voice_whisper_model",
    "touch_burst_timeout",
    "touch_correction_window",
)

# What to call each of them in the notice. Paired with _RESTART_KEYS by a
# test, so a key added above without a label is a test failure rather than a
# KeyError in front of the user at save time.
_RESTART_LABELS: dict[str, str] = {
    "hotkey_capture": "Quick capture hotkey",
    "hotkey_all_monitors": "All monitors hotkey",
    "hotkey_region": "Region capture hotkey",
    "hotkey_pause": "Pause/Resume hotkey",
    "voice_hotkey": "Dictate hotkey",
    "voice_fix_hotkey": "Fix last hotkey",
    "voice_whisper_model": "Whisper model",
    "touch_burst_timeout": "Touch burst timeout",
    "touch_correction_window": "Touch correction window",
}


def show_settings() -> None:
    """Show the settings dialog. Prevents duplicate windows.

    This may be called from a daemon thread (pystray menu callback).
    Tk is not thread-safe, so we catch all exceptions to prevent the
    dialog from taking down the daemon process when it closes.
    """
    global _settings_open
    if _settings_open:
        return
    _settings_open = True
    try:
        _build_and_run()
    except Exception:
        logger.exception("Settings dialog error (swallowed to protect daemon)")
    finally:
        _settings_open = False


def _section_header(parent: tk.Frame, text: str) -> None:
    """Add a section header label."""
    gui_theme.make_label(
        parent, text,
        font=("Segoe UI", 12, "bold"), fg=gui_theme.ACCENT,
    ).pack(anchor="w", pady=(15, 5))


def _spin_range(key: str) -> tuple[int, int]:
    """Spinner (from_, to) for a config key, taken from the core clamp table.

    Every spinner used to be built `from_=0, to=300`, one literal shared by
    auto_interval, jpeg_quality and buffer_max_age -- whose default is 1800.
    Touching the buffer spinner at all snapped 1800 down to 300 and there was
    no way back up through the control, so the dialog silently rewrote the
    setting it was showing. (David's saved `buffer_max_age: 300` is almost
    certainly that ceiling and not a preference.)

    Reading _CLAMPS makes the widget and the validator one declaration
    instead of two: a spinner cannot offer a value load_config() would clamp,
    and cannot refuse one it would accept. An unbounded key gets a 24h
    ceiling -- every spin field except jpeg_quality is seconds-valued, and
    jpeg_quality is bounded in the table. The min()/max() against the
    declared default is belt and braces: whatever the two tables say, a
    spinner must always be able to show the value it is seeded with.
    """
    lo, hi = _CLAMPS[key]
    default = int(_DEFAULTS[key])
    from_ = min(int(lo) if lo is not None else 0, default)
    to = max(int(hi) if hi is not None else _UNBOUNDED_SPIN_CEILING, default)
    return from_, to


def _field_row(
    parent: tk.Frame,
    label_text: str,
    var: tk.Variable,
    *,
    width: int = 0,
    entry_type: str = "entry",
    values: list[str] | None = None,
    config_key: str | None = None,
) -> tk.Widget:
    """Add a label + input row. Returns the input widget.

    `config_key` is REQUIRED for a spinner and names the key it edits; that
    is what ties the widget's limits to _CLAMPS. Raising rather than falling
    back to a literal is deliberate -- the 0..300 ceiling was invisible for
    as long as it was precisely because nothing connected a spinner to its
    key, and a silent default here would let the next one in the same way.
    """
    if entry_type == "spin" and config_key is None:
        raise ValueError(f"_field_row({label_text!r}, entry_type='spin') needs a config_key")
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
        spin_from, spin_to = _spin_range(config_key)
        widget = tk.Spinbox(
            row, textvariable=var,
            from_=spin_from, to=spin_to, increment=1,
            font=("Consolas", 10), width=width or 6,
            bg=gui_theme.SURFACE, fg=gui_theme.TEXT,
            insertbackground=gui_theme.ACCENT, relief="flat",
        )
        widget.pack(side="left")
    else:
        widget = gui_theme.make_entry(row, var)
        widget.pack(side="left", fill="x", expand=True, ipady=4)

    return widget


def _as_float(raw: str, key: str) -> float:
    """Parse a free-text numeric field, falling back to the declared default.

    The touch fields are plain Entry widgets, so `float()` on their contents
    raises ValueError for anything non-numeric. show_settings() swallows every
    exception to protect the daemon, so an unparseable "1,5" in one field used
    to discard the ENTIRE save -- blocklist, hotkeys and all -- with no message
    and no log line above DEBUG.
    """
    text = str(raw).strip()
    if not text:
        return float(_DEFAULTS[key])
    try:
        return float(text)
    except ValueError:
        logger.warning("Settings: %s=%r is not a number — keeping %r", key, text, _DEFAULTS[key])
        return float(_DEFAULTS[key])


def _build_and_run() -> None:
    # load_config() fills EVERY key in _DEFAULTS, so every read below is
    # cfg["key"] and not cfg.get("key", <literal>). The literals were a second
    # declaration site that had already drifted: this dialog offered
    # voice_whisper_model="base" and jpeg_quality=75 while the daemon ran
    # "small" and 90, so opening Settings and pressing Save silently
    # downgraded both. A KeyError here would mean _DEFAULTS lost a key the
    # dialog exposes, which is worth failing loudly for.
    cfg = load_config()

    dlg = gui_theme.create_dialog("ContextPulse — Settings", width=560, height=780)

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

    interval_var = tk.IntVar(master=root, value=cfg["auto_interval"])
    _field_row(frame, "Auto-capture interval (s):", interval_var,
               entry_type="spin", config_key="auto_interval")

    storage_var = tk.StringVar(master=root, value=cfg["storage_mode"])
    _field_row(
        frame, "Storage mode:", storage_var,
        entry_type="combo", values=["smart", "visual", "both", "text"],
    )

    gui_theme.make_label(
        frame,
        "smart  — screenshot only when screen changes significantly + OCR text (recommended)\n"
        "text   — OCR text only, no screenshots (~10 MB/day, fastest search)\n"
        "visual — screenshots only, no OCR (good for design/video work)\n"
        "both   — screenshots + OCR every capture (~100 MB/day, most searchable)",
        font=("Consolas", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 8))

    quality_var = tk.IntVar(master=root, value=cfg["jpeg_quality"])
    _field_row(frame, "JPEG quality (1-100):", quality_var,
               entry_type="spin", config_key="jpeg_quality")

    buffer_var = tk.IntVar(master=root, value=cfg["buffer_max_age"])
    _field_row(frame, "Buffer max age (seconds):", buffer_var,
               entry_type="spin", config_key="buffer_max_age")

    # ── Hotkeys Section ───────────────────────────────────────────
    _section_header(frame, "Hotkeys")

    hk_capture_var = tk.StringVar(master=root, value=cfg["hotkey_capture"])
    _field_row(frame, "Quick capture:", hk_capture_var)

    hk_all_var = tk.StringVar(master=root, value=cfg["hotkey_all_monitors"])
    _field_row(frame, "All monitors:", hk_all_var)

    hk_region_var = tk.StringVar(master=root, value=cfg["hotkey_region"])
    _field_row(frame, "Region capture:", hk_region_var)

    hk_pause_var = tk.StringVar(master=root, value=cfg["hotkey_pause"])
    _field_row(frame, "Pause/Resume:", hk_pause_var)

    gui_theme.make_label(
        frame, "Hotkey changes take effect after restart.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))


    # ── Voice Section ─────────────────────────────────────────────
    _section_header(frame, "Voice Dictation")

    voice_hotkey_var = tk.StringVar(master=root, value=cfg["voice_hotkey"])
    _field_row(frame, "Dictate (hold):", voice_hotkey_var)

    voice_fix_var = tk.StringVar(master=root, value=cfg["voice_fix_hotkey"])
    _field_row(frame, "Fix last:", voice_fix_var)

    voice_model_var = tk.StringVar(master=root, value=cfg["voice_whisper_model"])
    _field_row(
        frame, "Whisper model:", voice_model_var,
        entry_type="combo", values=["tiny", "base", "small", "medium", "large-v3"],
    )

    voice_llm_var = tk.StringVar(master=root, value="1" if cfg["voice_always_use_llm"] else "0")
    tk.Checkbutton(
        frame, text="  Always use AI cleanup (requires Anthropic API key)",
        variable=voice_llm_var, onvalue="1", offvalue="0",
        font=("Segoe UI", 10),
        fg=gui_theme.TEXT, bg=gui_theme.BG, selectcolor=gui_theme.BG,
        activebackground=gui_theme.BG, activeforeground=gui_theme.TEXT,
        highlightthickness=0, bd=1,
    ).pack(anchor="w", pady=(8, 0))

    voice_api_var = tk.StringVar(master=root, value=cfg["voice_anthropic_api_key"])
    _field_row(frame, "Anthropic API key:", voice_api_var)

    gui_theme.make_label(
        frame,
        "tiny      — fastest, ~40 MB RAM, fine for short commands, struggles with names/jargon\n"
        "base      — ~150 MB RAM, strong accuracy for everyday speech\n"
        "small     — default, better with accents and technical terms, ~500 MB RAM, ~2x slower\n"
        "medium    — near-human accuracy, ~1.5 GB RAM, noticeable pause on long dictations\n"
        "large-v3  — highest accuracy, ~3 GB RAM, slow on CPU — best with a GPU\n"
        "Model changes take effect after restart.",
        font=("Consolas", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    # ── Touch Section ─────────────────────────────────────────────
    _section_header(frame, "Touch (Input Capture)")

    burst_var = tk.StringVar(master=root, value=str(cfg["touch_burst_timeout"]))
    _field_row(frame, "Burst timeout (s):", burst_var)

    correction_var = tk.StringVar(master=root, value=str(cfg["touch_correction_window"]))
    _field_row(frame, "Correction window (s):", correction_var)

    gui_theme.make_label(
        frame,
        "Touch captures typing patterns and detects voice dictation corrections.\n"
        "Timing changes take effect after restart.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    # ── Privacy Section ───────────────────────────────────────────
    _section_header(frame, "Privacy")

    blocklist_str = ", ".join(cfg["blocklist_patterns"])
    blocklist_var = tk.StringVar(master=root, value=blocklist_str)
    _field_row(frame, "Blocklist (comma-sep):", blocklist_var)

    always_both_str = ", ".join(cfg["always_both_apps"])
    always_both_var = tk.StringVar(master=root, value=always_both_str)
    _field_row(frame, "Always keep image+text:", always_both_var)

    gui_theme.make_label(
        frame, "Blocklist: window titles containing these strings are never captured.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    redact_var = tk.StringVar(master=root, value="1" if cfg["redact_ocr_text"] else "0")
    tk.Checkbutton(
        frame, text="  Redact sensitive text from OCR (API keys, passwords, tokens)",
        variable=redact_var, onvalue="1", offvalue="0",
        font=("Segoe UI", 10),
        fg=gui_theme.TEXT, bg=gui_theme.BG, selectcolor=gui_theme.BG,
        activebackground=gui_theme.BG, activeforeground=gui_theme.TEXT,
        highlightthickness=0, bd=1,
    ).pack(anchor="w", pady=(8, 0))

    clipboard_var = tk.StringVar(
        master=root, value="1" if cfg["clipboard_enabled"] else "0"
    )
    tk.Checkbutton(
        frame, text="  Capture clipboard contents",
        variable=clipboard_var, onvalue="1", offvalue="0",
        font=("Segoe UI", 10),
        fg=gui_theme.TEXT, bg=gui_theme.BG, selectcolor=gui_theme.BG,
        activebackground=gui_theme.BG, activeforeground=gui_theme.TEXT,
        highlightthickness=0, bd=1,
    ).pack(anchor="w", pady=(4, 0))

    gui_theme.make_label(
        frame,
        "Clipboard text is always scanned for secrets before it is stored — "
        "that is not optional. This switch controls capture itself.",
        font=("Segoe UI", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

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
        license_text = "Memory trial expired — enter a license key to continue"
        license_color = gui_theme.ERROR

    gui_theme.make_label(
        frame, license_text,
        font=("Segoe UI", 10), fg=license_color,
    ).pack(anchor="w", pady=(0, 4))

    # What's included
    gui_theme.make_label(
        frame,
        "Free forever  — memory store, recall, list, forget (no license needed)\n"
        "Pro ($49/yr)   — adds semantic search + cross-modal search  |  Lifetime: $249\n"
        "Sight (screen capture) is always free — no license required.",
        font=("Consolas", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(0, 8))

    # Buttons row
    lic_btn_frame = tk.Frame(frame, bg=gui_theme.BG)
    lic_btn_frame.pack(anchor="w", pady=(0, 5))

    def open_license_dialog():
        from contextpulse_core.license_dialog import show_nag_dialog
        show_nag_dialog()
        dlg.destroy()

    def open_purchase_page():
        import webbrowser
        webbrowser.open("https://contextpulse.ai/pricing")

    ttk.Button(
        lic_btn_frame, text="Buy / Upgrade", style="Accent.TButton",
        command=open_purchase_page,
    ).pack(side="left", padx=(0, 10))

    ttk.Button(
        lic_btn_frame, text="Enter License Key", style="Secondary.TButton",
        command=open_license_dialog,
    ).pack(side="left")

    # ── MCP Access Section ────────────────────────────────────────
    # This section is read-only state, not config: the token lives in its own
    # file, never in config.json, so save_and_close() must not touch it.
    _section_header(frame, "MCP Access")

    gui_theme.make_label(
        frame,
        "Your AI agent needs this token to reach ContextPulse. Anything holding\n"
        "it can call every tool, so treat it like a password.",
        font=("Segoe UI", 9), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(0, 6))

    token_state = {"value": "", "shown": False}
    try:
        token_state["value"] = mcp_auth.load_or_create_token()
    except (OSError, RuntimeError):
        logger.exception("Could not load the MCP access token")

    token_var = tk.StringVar(master=root)

    def _render_token() -> None:
        token = token_state["value"]
        if not token:
            token_var.set("unavailable — see the log")
        elif token_state["shown"]:
            token_var.set(token)
        else:
            token_var.set(f"{token[:4]}{'•' * 24}{token[-4:]}")

    _render_token()

    tk.Label(
        frame, textvariable=token_var,
        font=("Consolas", 9), fg=gui_theme.TEXT, bg=gui_theme.SURFACE,
        anchor="w", padx=8, pady=6,
    ).pack(fill="x", pady=(0, 6))

    mcp_btn_frame = tk.Frame(frame, bg=gui_theme.BG)
    mcp_btn_frame.pack(anchor="w", pady=(0, 5))

    show_btn: dict = {}

    def toggle_show() -> None:
        token_state["shown"] = not token_state["shown"]
        _render_token()
        show_btn["w"].config(text="Hide" if token_state["shown"] else "Show")

    def copy_snippet() -> None:
        """Copy the Claude Code snippet under the clipboard lock.

        Not pyperclip directly: this dialog is open while the sight poller is
        reading the clipboard every second, and pyperclip.copy's
        EmptyClipboard frees handles the poller may be holding -- the
        0xC0000374 heap corruption that takes the daemon down with no
        traceback.
        """
        if not token_state["value"]:
            return
        snippet = mcp_auth.config_snippet("claude-code", token=token_state["value"])
        if clipboard_lock.copy_text(snippet, what="the MCP client config"):
            messagebox.showinfo(
                "ContextPulse",
                "Claude Code config copied. Paste it into ~/.claude.json, then\n"
                "reconnect contextpulse in the /mcp panel.",
            )
        else:
            messagebox.showerror(
                "ContextPulse",
                "Clipboard busy — nothing was copied.\n\n"
                "Try again, or run:  contextpulse-mcp --print-config claude-code",
            )

    def regenerate() -> None:
        if not messagebox.askyesno(
            "ContextPulse",
            "Generate a new access token?\n\n"
            "The old token stops working immediately — the running MCP server\n"
            "picks up the change without a restart. Every client configured\n"
            "with it stops working until you re-run  contextpulse --setup\n"
            "and reconnect.",
        ):
            return
        try:
            token_state["value"] = mcp_auth.regenerate_token()
        except (OSError, RuntimeError):
            logger.exception("Could not regenerate the MCP access token")
            messagebox.showerror("ContextPulse", "Could not regenerate the token — see the log.")
            return
        token_state["shown"] = False
        _render_token()
        show_btn["w"].config(text="Show")
        messagebox.showinfo(
            "ContextPulse",
            "New token generated. The old one is already refused.\n\n"
            "1. Run  contextpulse --setup  to update your clients\n"
            "2. Reconnect the client (no server restart needed)",
        )

    show_btn["w"] = ttk.Button(
        mcp_btn_frame, text="Show", style="Secondary.TButton", command=toggle_show,
    )
    show_btn["w"].pack(side="left", padx=(0, 10))

    ttk.Button(
        mcp_btn_frame, text="Copy Claude Code snippet", style="Accent.TButton",
        command=copy_snippet,
    ).pack(side="left", padx=(0, 10))

    ttk.Button(
        mcp_btn_frame, text="Regenerate token", style="Secondary.TButton",
        command=regenerate,
    ).pack(side="left")

    gui_theme.make_label(
        frame,
        f"Token file: {mcp_auth.TOKEN_FILE}",
        font=("Consolas", 8), fg=gui_theme.TEXT_MUTED,
    ).pack(anchor="w", pady=(6, 0))

    # ── Save & Close ──────────────────────────────────────────────
    # Values of the restart-bound keys as they were when the dialog opened,
    # so save_and_close can name exactly which of them the user changed.
    startup_values = {key: cfg[key] for key in _RESTART_KEYS}

    def save_and_close():
        new_cfg = dict(cfg)  # preserve any unknown keys
        new_cfg.update({
            # Capture
            "auto_interval": max(0, interval_var.get()),
            "storage_mode": storage_var.get(),
            "jpeg_quality": max(1, min(100, quality_var.get())),
            "buffer_max_age": max(0, buffer_var.get()),
            # Sight hotkeys
            "hotkey_capture": hk_capture_var.get().strip().lower() or _DEFAULTS["hotkey_capture"],
            "hotkey_all_monitors": hk_all_var.get().strip().lower() or _DEFAULTS["hotkey_all_monitors"],
            "hotkey_region": hk_region_var.get().strip().lower() or _DEFAULTS["hotkey_region"],
            "hotkey_pause": hk_pause_var.get().strip().lower() or _DEFAULTS["hotkey_pause"],
            # Voice
            "voice_hotkey": voice_hotkey_var.get().strip().lower() or _DEFAULTS["voice_hotkey"],
            "voice_fix_hotkey": voice_fix_var.get().strip().lower() or _DEFAULTS["voice_fix_hotkey"],
            "voice_whisper_model": voice_model_var.get() or _DEFAULTS["voice_whisper_model"],
            "voice_always_use_llm": voice_llm_var.get() == "1",
            "voice_anthropic_api_key": voice_api_var.get().strip(),
            # Touch
            "touch_burst_timeout": _as_float(burst_var.get(), "touch_burst_timeout"),
            "touch_correction_window": _as_float(correction_var.get(), "touch_correction_window"),
            # Privacy
            "blocklist_patterns": [p.strip() for p in blocklist_var.get().split(",") if p.strip()],
            "always_both_apps": [p.strip() for p in always_both_var.get().split(",") if p.strip()],
            "redact_ocr_text": redact_var.get() == "1",
            "clipboard_enabled": clipboard_var.get() == "1",
        })
        save_config(new_cfg)
        logger.info("Settings saved")

        changed = [k for k in _RESTART_KEYS if new_cfg[k] != startup_values[k]]
        if changed:
            messagebox.showinfo(
                "ContextPulse",
                "Saved. These take effect after you restart ContextPulse:\n\n  "
                + "\n  ".join(_RESTART_LABELS[k] for k in changed)
                + "\n\nEverything else you changed is already live.",
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

    # Unbind mousewheel on close to prevent errors.
    # CRITICAL: do NOT destroy the hidden Tk root — only destroy the
    # Toplevel dialog.  Destroying root kills pystray's message pump
    # and takes down the entire daemon process (exit code 0).
    def _on_close():
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        try:
            dlg.destroy()
        except Exception:
            pass

    dlg.protocol("WM_DELETE_WINDOW", _on_close)
    try:
        dlg.wait_window()
    except Exception:
        logger.debug("Settings wait_window interrupted", exc_info=True)
