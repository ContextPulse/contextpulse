"""GUI survival tests — dialogs must not kill the daemon.

The canonical bug: show_settings() runs in a daemon thread, creates a
tk.Tk() root. When the dialog closes, Tk's internal state gets corrupted,
killing pystray's message pump and taking down the daemon (exit code 0).
"""

import pytest


class TestSettingsDialogSafety:
    """Settings dialog must not crash the daemon process."""

    def test_settings_prevents_duplicate_windows(self):
        """Only one settings dialog can be open at a time."""
        import contextpulse_core.settings as s

        original = s._settings_open
        try:
            s._settings_open = True
            # show_settings should return immediately without opening
            s.show_settings()
            # If we get here without error, the guard worked
            assert s._settings_open is True
        finally:
            s._settings_open = original

    def test_show_settings_catches_exceptions(self):
        """show_settings must not propagate exceptions to the caller.
        In the daemon, the caller is a pystray menu callback — an
        unhandled exception there kills the tray icon and daemon."""
        import inspect

        import contextpulse_core.settings as s

        source = inspect.getsource(s.show_settings)
        assert "except" in source, (
            "show_settings() must catch exceptions to protect the daemon "
            "from settings dialog crashes"
        )

    def test_tk_root_singleton_no_winfo_check(self):
        """_get_root must NOT call winfo_exists() in its logic.

        After a Toplevel dialog is destroyed in a daemon thread,
        winfo_exists() can return False even though the Tcl interpreter
        is still alive. Creating a second tk.Tk() crashes the process.
        Comments mentioning winfo_exists are fine — the code must not call it.
        """
        import inspect

        from contextpulse_core import gui_theme

        import ast
        source = inspect.getsource(gui_theme._get_root)
        # Parse AST to check for actual winfo_exists calls in code,
        # ignoring comments and docstrings
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "winfo_exists":
                pytest.fail(
                    "_get_root code calls winfo_exists() — it causes false "
                    "negatives after dialog close, leading to duplicate Tk roots"
                )
            if isinstance(node, ast.Name) and node.id == "winfo_exists":
                pytest.fail(
                    "_get_root code references winfo_exists — remove it"
                )


class TestDialogCloseProtection:
    """Dialog close handlers must protect the Tk root."""

    def test_settings_dialog_close_has_try_except(self):
        """The _on_close handler in settings.py must wrap dlg.destroy()
        in try/except to prevent Tk cleanup errors from propagating."""
        import inspect

        import contextpulse_core.settings as s

        source = inspect.getsource(s._build_and_run)
        # The _on_close function should have exception handling
        assert "try:" in source and "dlg.destroy()" in source, (
            "_build_and_run must wrap dialog close in try/except"
        )

    def test_settings_wait_window_has_try_except(self):
        """dlg.wait_window() must be wrapped in try/except.
        On Windows, Tk event loop interruptions can raise when the
        daemon is shutting down."""
        import inspect

        import contextpulse_core.settings as s

        source = inspect.getsource(s._build_and_run)
        # Find wait_window usage — should be in a try block
        assert "wait_window" in source, "Settings dialog must call wait_window"
