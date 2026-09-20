# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The poll must not consume a clipboard change it never actually read.

Fallout from cp-daemon-heap-corruption-after-paste: the Win32 read now
returns None when a paste holds the clipboard lock. The poller used to
commit the new sequence number BEFORE reading, so a skipped read would
retire that clipboard change forever — the dictation the user just pasted
would never be captured. Commit the sequence only once a read succeeds, so
the next tick retries.
"""

from unittest.mock import MagicMock

import pytest
from contextpulse_sight import clipboard as clipboard_mod
from contextpulse_sight.clipboard import ClipboardMonitor


@pytest.fixture
def monitor():
    return ClipboardMonitor(activity_db=MagicMock())


class TestSequenceNumberIsNotConsumedByASkippedRead:
    def test_none_read_leaves_the_change_pending(self, monkeypatch, monitor):
        """A read that returns nothing must be retried on the next tick."""
        monkeypatch.setattr(clipboard_mod, "_get_clipboard_sequence", lambda: 42)
        monkeypatch.setattr(clipboard_mod, "_get_clipboard_text", lambda: None)

        monitor._check_clipboard()

        assert monitor._sequence_number == 0, (
            "sequence advanced despite reading nothing — the clipboard change "
            "the lock made us skip would never be captured"
        )

    def test_the_retry_captures_the_text(self, monkeypatch, monitor):
        """Tick 1 is blocked, tick 2 succeeds and the text lands."""
        monkeypatch.setattr(clipboard_mod, "_get_clipboard_sequence", lambda: 42)
        reads = iter([None, "the dictated sentence"])
        monkeypatch.setattr(clipboard_mod, "_get_clipboard_text", lambda: next(reads))

        monitor._check_clipboard()
        monitor._check_clipboard()

        assert monitor._sequence_number == 42
        monitor._activity_db.record_clipboard.assert_called_once()
        assert (
            monitor._activity_db.record_clipboard.call_args.kwargs["text"]
            == "the dictated sentence"
        )

    def test_unchanged_sequence_still_short_circuits(self, monkeypatch, monitor):
        """The cheap sequence check must still avoid a pointless read."""
        monitor._sequence_number = 42
        monkeypatch.setattr(clipboard_mod, "_get_clipboard_sequence", lambda: 42)

        def must_not_run():
            raise AssertionError("read attempted despite an unchanged sequence")

        monkeypatch.setattr(clipboard_mod, "_get_clipboard_text", must_not_run)

        monitor._check_clipboard()
