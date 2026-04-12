# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""macOS menu bar icon using rumps.

rumps.App.run() must execute on the main thread (AppKit requirement).
The daemon restructures so: main thread = menu bar, background threads = modules.
"""

import subprocess

import rumps


class ContextPulseMenuBar(rumps.App):
    """macOS status bar app for ContextPulse."""

    def __init__(self, daemon):
        super().__init__("ContextPulse", icon=None, quit_button=None)
        self.daemon = daemon
        self.menu = [
            rumps.MenuItem("Pause Capture", callback=self.toggle_pause),
            rumps.MenuItem("Open Screenshots", callback=self.open_screenshots),
            rumps.MenuItem("Settings...", callback=self.open_settings),
            None,  # separator
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

    def toggle_pause(self, sender):
        self.daemon.toggle_pause()
        sender.title = "Resume Capture" if self.daemon.paused else "Pause Capture"

    def open_screenshots(self, _):
        subprocess.run(["open", str(self.daemon.output_dir)])

    def open_settings(self, _):
        from contextpulse_core.settings import show_settings
        show_settings()

    def quit_app(self, _):
        self.daemon.shutdown()
        rumps.quit_application()


def create_tray(daemon):
    """Create and return the macOS menu bar app. Call .run() on main thread."""
    return ContextPulseMenuBar(daemon)
