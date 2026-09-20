"""Clipboard secrets must never reach storage, the FTS index, or an MCP client.

Reported privately by an external researcher 2026-09-19: clipboard capture bypassed
the secret redaction applied to OCR text. `redact_sensitive()` existed and had
exactly one caller (ocr_worker), so every pattern family the product advertises
as "redacted before storage" was being written verbatim to the `clipboard`
table, to `events.payload`, into `events_fts`, and handed back raw by four MCP
tools.

Every value below is SYNTHETIC. The shape has to be real enough to trip the
pattern; the value never is.

Each case asserts the secret appears in NONE of:
  1. the `clipboard` table
  2. the `events` table (payload JSON)
  3. the `events_fts` full-text index (probed by MATCH, not by reading rows --
     an external-content FTS table reads columns back from `events`, so a
     SELECT would re-read the redacted payload and pass vacuously)
  4. get_clipboard_history / search_clipboard / search_all_events /
     get_event_timeline
  5. the raw bytes of the database file on disk (backstop: catches anything
     the four structured checks above do not know to look at)

Check 3 carries a positive control (`zqcontrol`, present in every clip and
outside every redacted span) that MUST match. Without it, "0 rows matched"
would be equally consistent with "the index is empty" and the whole check
would be decoration.
"""

import json
import sqlite3
import time
from unittest.mock import patch

import pytest
from contextpulse_sight.redact import redact_sensitive

# The word every clipboard sample carries, outside every redactable span.
# Its presence in the FTS index proves a MATCH probe can find things at all.
CONTROL_WORD = "zqcontrol"

# (family, clipboard text, the exact substring that must disappear,
#  an FTS term drawn from that substring that must not match)
SECRET_CASES = [
    (
        "aws_access_key",
        f"{CONTROL_WORD} deploy notes\nAKIAZQAWSNEEDLE01234 is the key\n",
        "AKIAZQAWSNEEDLE01234",
        "akiazqawsneedle01234",
    ),
    (
        "aws_secret_key",
        f"{CONTROL_WORD} deploy notes\naws_secret_access_key = zqawssecretneedle0123456789abcd\n",
        "zqawssecretneedle0123456789abcd",
        "zqawssecretneedle0123456789abcd",
    ),
    (
        "openai_style_key",
        f"{CONTROL_WORD} scratch\nsk-zqopenaineedle0123456789ABCD\n",
        "sk-zqopenaineedle0123456789ABCD",
        "zqopenaineedle0123456789ABCD",
    ),
    (
        "anthropic_style_key",
        f"{CONTROL_WORD} scratch\nsk-ant-zqanthropicneedle0123456789\n",
        "sk-ant-zqanthropicneedle0123456789",
        "zqanthropicneedle0123456789",
    ),
    (
        "github_ghp_token",
        f"{CONTROL_WORD} scratch\nghp_zqgithubneedle0123456789abcdefghijklmn\n",
        "ghp_zqgithubneedle0123456789abcdefghijklmn",
        "zqgithubneedle0123456789abcdefghijklmn",
    ),
    (
        "github_gho_token",
        f"{CONTROL_WORD} scratch\ngho_zqgithuboauth0123456789abcdefghijklmn\n",
        "gho_zqgithuboauth0123456789abcdefghijklmn",
        "zqgithuboauth0123456789abcdefghijklmn",
    ),
    (
        "password_colon_value",
        f"{CONTROL_WORD} creds\npassword: zqpasswordneedle99\n",
        "zqpasswordneedle99",
        "zqpasswordneedle99",
    ),
    (
        "api_key_equals_value",
        f"{CONTROL_WORD} creds\napi_key=zqapikeyneedle77\n",
        "zqapikeyneedle77",
        "zqapikeyneedle77",
    ),
    (
        "token_colon_value",
        f"{CONTROL_WORD} creds\ntoken: zqtokenneedle55\n",
        "zqtokenneedle55",
        "zqtokenneedle55",
    ),
    (
        "secret_colon_value",
        f"{CONTROL_WORD} creds\nsecret: zqsecretneedle44\n",
        "zqsecretneedle44",
        "zqsecretneedle44",
    ),
    (
        "jwt",
        f"{CONTROL_WORD} headers\n"
        "eyJzcXpxand0bmVlZGxlMDEyMzQ1.eyJzdWIiOiJ6cWp3dGJvZHluZWVkbGUifQ."
        "zqjwtsignatureneedle0123456789\n",
        "eyJzcXpxand0bmVlZGxlMDEyMzQ1.eyJzdWIiOiJ6cWp3dGJvZHluZWVkbGUifQ."
        "zqjwtsignatureneedle0123456789",
        "zqjwtsignatureneedle0123456789",
    ),
    (
        "bearer_token",
        f"{CONTROL_WORD} headers\nAuthorization: Bearer zqbearerneedle0123456789\n",
        "zqbearerneedle0123456789",
        "zqbearerneedle0123456789",
    ),
    (
        "credit_card",
        f"{CONTROL_WORD} checkout\n4111-1111-1111-1111\n",
        "4111-1111-1111-1111",
        "4111",
    ),
    (
        "ssn",
        f"{CONTROL_WORD} form\n987-65-4321\n",
        "987-65-4321",
        "4321",
    ),
    (
        "private_key",
        f"{CONTROL_WORD} pem\n-----BEGIN RSA PRIVATE KEY-----\n"
        "zqprivatekeyneedle0123456789abcdef\n-----END RSA PRIVATE KEY-----\n",
        "zqprivatekeyneedle0123456789abcdef",
        "zqprivatekeyneedle0123456789abcdef",
    ),
    (
        "connection_string",
        f"{CONTROL_WORD} db\npostgres://appuser:zqconnstrneedle88@db.invalid:5432/app\n",
        "zqconnstrneedle88",
        "zqconnstrneedle88",
    ),
]

CASE_IDS = [c[0] for c in SECRET_CASES]


class TestRedactPatternsCoverSyntheticValues:
    """Guard on the fixtures themselves.

    If a synthetic value does not trip its pattern, every downstream assertion
    in this file becomes a statement about a value that was never a secret.
    This test is what makes the rest of the file mean something.
    """

    @pytest.mark.parametrize("family,clip_text,secret,_probe", SECRET_CASES, ids=CASE_IDS)
    def test_pattern_removes_secret(self, family, clip_text, secret, _probe):
        cleaned = redact_sensitive(clip_text)
        assert secret not in cleaned, f"{family}: redact_sensitive left the value intact"
        assert "[REDACTED" in cleaned, f"{family}: nothing was redacted at all"

    @pytest.mark.parametrize("family,clip_text,_s,_p", SECRET_CASES, ids=CASE_IDS)
    def test_control_word_survives_redaction(self, family, clip_text, _s, _p):
        # The positive control must not itself be redacted, or check 3 in
        # every integration test below silently loses its denominator.
        assert CONTROL_WORD in redact_sensitive(clip_text)


def _read_db_bytes(db_path):
    """Return the on-disk bytes of the database, including any WAL sidecar."""
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            blob += sidecar.read_bytes()
    return blob


def _capture_one_tick(tmp_path, clip_text):
    """Drive exactly one ClipboardMonitor tick against a temp DB.

    Returns a dict of every artifact the secret could have landed in, with
    all connections closed so the on-disk bytes are final.
    """
    from contextpulse_core.spine import EventBus
    from contextpulse_sight import mcp_server
    from contextpulse_sight.activity import ActivityDB
    from contextpulse_sight.clipboard import ClipboardMonitor
    from contextpulse_sight.sight_module import SightModule

    db_path = tmp_path / "activity.db"
    db = ActivityDB(db_path=db_path)
    bus = EventBus(db_path)
    module = SightModule()
    module.register(bus.emit)
    module.start()

    monitor = ClipboardMonitor(db)
    monitor.set_sight_module(module)
    monitor._sequence_number = 0
    monitor._last_capture_time = 0.0
    monitor._last_text = ""

    with (
        patch("contextpulse_sight.clipboard._get_clipboard_sequence", return_value=1),
        patch("contextpulse_sight.clipboard._get_clipboard_text", return_value=clip_text),
    ):
        monitor._check_clipboard()

    clipboard_rows = [r["text"] for r in db.get_clipboard_history(count=50)]

    raw = sqlite3.connect(str(db_path))
    event_payloads = [r[0] for r in raw.execute("SELECT payload FROM events").fetchall()]

    def fts_hits(term):
        return raw.execute(
            "SELECT count(*) FROM events_fts WHERE events_fts MATCH ?", (term,)
        ).fetchone()[0]

    control_hits = fts_hits(CONTROL_WORD)
    raw.close()

    # MCP boundary — swap the module-level singletons for the temp DB.
    original_db = mcp_server._activity_db
    original_bus = mcp_server._event_bus
    mcp_server._activity_db = db
    mcp_server._event_bus = bus
    try:
        with patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True):
            responses = {
                "get_clipboard_history": mcp_server.get_clipboard_history(count=50),
                "search_clipboard": mcp_server.search_clipboard(
                    CONTROL_WORD, minutes_ago=60
                ),
                "search_all_events": mcp_server.search_all_events(
                    CONTROL_WORD, minutes_ago=60
                ),
                "get_event_timeline": mcp_server.get_event_timeline(minutes_ago=60),
            }
    finally:
        mcp_server._activity_db = original_db
        mcp_server._event_bus = original_bus

    module.stop()
    bus.close()
    db.close()

    return {
        "db_path": db_path,
        "clipboard_rows": clipboard_rows,
        "event_payloads": event_payloads,
        "control_hits": control_hits,
        "responses": responses,
        "db_bytes": _read_db_bytes(db_path),
    }


def _fts_probe_hits(db_path, term):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT count(*) FROM events_fts WHERE events_fts MATCH ?", (term,)
        ).fetchone()[0]
    finally:
        conn.close()


class TestSecretsNeverReachStorageOrMCP:
    """The class fix: one redaction in ClipboardMonitor covers every sink."""

    @pytest.mark.parametrize("family,clip_text,secret,probe", SECRET_CASES, ids=CASE_IDS)
    def test_secret_absent_everywhere(self, tmp_path, family, clip_text, secret, probe):
        art = _capture_one_tick(tmp_path, clip_text)

        # The tick must actually have captured something, or every "not in"
        # assertion below passes because nothing was stored at all.
        assert len(art["clipboard_rows"]) == 1, f"{family}: expected one clipboard row"
        assert len(art["event_payloads"]) == 1, f"{family}: expected one event row"

        # Positive control — the FTS index is populated and MATCH can find it.
        assert art["control_hits"] == 1, (
            f"{family}: control word not indexed; the MATCH probe below proves nothing"
        )

        assert secret not in art["clipboard_rows"][0], f"{family}: leaked to clipboard table"
        payload = art["event_payloads"][0]
        assert secret not in payload, f"{family}: leaked to events.payload"
        assert secret not in json.loads(payload).get("text", ""), (
            f"{family}: leaked to the event text field"
        )

        assert _fts_probe_hits(art["db_path"], probe) == 0, (
            f"{family}: secret is discoverable through events_fts"
        )

        for tool, response in art["responses"].items():
            assert secret not in str(response), f"{family}: leaked through MCP tool {tool}"

        assert secret.encode() not in art["db_bytes"], (
            f"{family}: secret found in the raw database file"
        )


class TestMonitorAccessorRedactsPreFixRows:
    """ClipboardMonitor.get_recent() reads rows back out of the store."""

    def test_get_recent_masks_a_raw_row(self, tmp_path):
        from contextpulse_sight.activity import ActivityDB
        from contextpulse_sight.clipboard import ClipboardMonitor

        secret = "sk-zqaccessorneedle0123456789ABCD"
        db = ActivityDB(db_path=tmp_path / "activity.db")
        try:
            # Straight to the table, bypassing the monitor -- a pre-fix row.
            db.record_clipboard(timestamp=time.time(), text=f"{CONTROL_WORD}\n{secret}\n")
            entries = ClipboardMonitor(db).get_recent(count=5)
        finally:
            db.close()

        assert len(entries) == 1
        assert CONTROL_WORD in entries[0]["text"], "returned nothing to redact"
        assert secret not in entries[0]["text"]
        # Other columns must survive the rewrite.
        assert "timestamp" in entries[0] and "id" in entries[0]


class TestPreFixRowsAreRedactedAtTheMCPBoundary:
    """Rows written before the fix are still raw on disk.

    The integration test above cannot cover this: it drives the fixed monitor,
    so the store is already clean and its MCP assertions would pass even with
    no boundary redaction at all. These tests write RAW rows straight to the
    tables -- the way the daemon did until this branch -- and assert the tools
    scrub them on the way out.
    """

    def _raw_store(self, tmp_path, secret_text):
        from contextpulse_core.spine import EventBus
        from contextpulse_sight.activity import ActivityDB
        from contextpulse_sight.sight_module import SightModule

        db_path = tmp_path / "activity.db"
        db = ActivityDB(db_path=db_path)
        bus = EventBus(db_path)
        module = SightModule()
        module.register(bus.emit)
        module.start()

        now = time.time()
        # Straight past ClipboardMonitor -- exactly what pre-fix rows look like.
        db.record_clipboard(timestamp=now, text=secret_text)
        module.emit_clipboard(timestamp=now, text=secret_text, hash_val="deadbeef")
        module.emit_ocr(
            timestamp=now,
            frame_path="/tmp/f.jpg",
            ocr_text=secret_text,
            confidence=0.9,
            app_name="Terminal",
            window_title=f"{CONTROL_WORD} shell",
        )
        db.record(
            timestamp=now,
            window_title=f"{CONTROL_WORD} {secret_text}",
            app_name="Terminal",
            monitor_index=0,
            frame_path="/tmp/f.jpg",
        )
        return db, bus, module

    @pytest.mark.parametrize("family,clip_text,secret,_p", SECRET_CASES, ids=CASE_IDS)
    def test_tools_scrub_raw_rows(self, tmp_path, family, clip_text, secret, _p):
        from contextpulse_sight import mcp_server

        # The stored row is the clipboard text verbatim -- the keyword-value
        # families (password:, api_key=, aws_secret_access_key=) are only
        # redactable with their keyword present, so planting the bare value
        # would test a string that was never a secret.
        db, bus, module = self._raw_store(tmp_path, clip_text)

        original_db = mcp_server._activity_db
        original_bus = mcp_server._event_bus
        mcp_server._activity_db = db
        mcp_server._event_bus = bus
        try:
            with (
                patch("contextpulse_sight.mcp_server.has_pro_access", return_value=True),
                patch("contextpulse_sight.mcp_server.is_title_blocked", return_value=False),
            ):
                responses = {
                    "get_clipboard_history": mcp_server.get_clipboard_history(count=50),
                    "search_clipboard": mcp_server.search_clipboard(
                        CONTROL_WORD, minutes_ago=60
                    ),
                    "search_all_events": mcp_server.search_all_events(
                        CONTROL_WORD, minutes_ago=60
                    ),
                    "get_event_timeline": mcp_server.get_event_timeline(minutes_ago=60),
                    "search_history": mcp_server.search_history(
                        CONTROL_WORD, minutes_ago=60
                    ),
                    "get_context_at": mcp_server.get_context_at(minutes_ago=0.1),
                    "get_activity_summary": mcp_server.get_activity_summary(hours=1.0),
                }
        finally:
            mcp_server._activity_db = original_db
            mcp_server._event_bus = original_bus
            module.stop()
            bus.close()
            db.close()

        # Positive control: the tools returned real content, so "secret not in
        # response" is a statement about redaction rather than about an empty
        # string. get_event_timeline prints no payload text, so it carries the
        # control only via the window title.
        for tool in ("get_clipboard_history", "search_clipboard", "search_all_events"):
            assert CONTROL_WORD in str(responses[tool]), (
                f"{family}: {tool} returned nothing to redact -- assertion is vacuous"
            )

        for tool, response in responses.items():
            assert secret not in str(response), (
                f"{family}: raw pre-fix row leaked through MCP tool {tool}"
            )


class TestRedactionPrecedesTruncation:
    """A token split by the 10,000-char cut must still be caught.

    Truncating first would hand the redactor half a token, which matches
    nothing, and write the leading half verbatim.
    """

    def test_secret_straddling_the_truncation_boundary_is_redacted(self, tmp_path):
        from contextpulse_sight.clipboard import _MAX_LENGTH

        # A GitHub token: the pattern needs 36+ characters after `ghp_`, so a
        # fragment cut short of that is invisible to the redactor. That is
        # exactly the leak truncate-then-redact produces.
        secret = "ghp_zqboundaryneedle0123456789abcdefghij"
        surviving_fragment = secret[:30]
        assert redact_sensitive(surviving_fragment) == surviving_fragment, (
            "fixture is wrong: the truncated fragment must NOT be redactable on "
            "its own, or this test cannot distinguish the two orderings"
        )

        # The token is separated by whitespace, not glued to the filler: every
        # pattern in redact.py is \b-anchored, so a token abutting a word
        # character is not matched at all (noted as a separate finding -- it
        # is a property of the redactor, not of the truncation ordering this
        # test exists to pin).
        head = f"{CONTROL_WORD} "
        filler = "x" * (_MAX_LENGTH - len(head) - 1 - len(surviving_fragment))
        clip_text = f"{head}{filler} {secret} trailing"
        assert clip_text.index(secret) + len(surviving_fragment) == _MAX_LENGTH, (
            "fixture is wrong: the truncation point must fall inside the token"
        )

        art = _capture_one_tick(tmp_path, clip_text)

        stored = art["clipboard_rows"][0]
        assert len(stored) <= _MAX_LENGTH + 64  # truncation still applied
        assert secret not in stored
        assert surviving_fragment not in stored
        assert secret.encode() not in art["db_bytes"]
        assert surviving_fragment.encode() not in art["db_bytes"]


class TestClipboardEnabledIsHonoured:
    """`clipboard_enabled` was declared in the config schema and read nowhere.

    ClipboardMonitor was constructed unconditionally in __init__ and restarted
    unconditionally by the watchdog, so the toggle a user could see in the
    module schema did nothing at all.
    """

    def _make_app(self, tmp_path, monkeypatch):
        import contextpulse_sight.activity as act
        import contextpulse_sight.buffer as buf_mod
        import contextpulse_sight.config as cfg

        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()
        monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
        monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

        from contextpulse_sight.app import ContextPulseSightApp

        return ContextPulseSightApp()

    def test_disabled_constructs_no_monitor(self, tmp_path, monkeypatch):
        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app = self._make_app(tmp_path, monkeypatch)
        assert app._clipboard_monitor is None

    def test_enabled_by_default(self, tmp_path, monkeypatch):
        app = self._make_app(tmp_path, monkeypatch)
        assert app._clipboard_monitor is not None

    def test_disabled_starts_no_thread(self, tmp_path, monkeypatch):
        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app = self._make_app(tmp_path, monkeypatch)
            with patch(
                "contextpulse_sight.app.ClipboardMonitor",
                side_effect=AssertionError("ClipboardMonitor must not be constructed"),
            ):
                app._start_clipboard_monitor()
        assert app._clipboard_monitor is None

    def test_disabled_watchdog_does_not_resurrect(self, tmp_path, monkeypatch):
        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app = self._make_app(tmp_path, monkeypatch)
            with patch(
                "contextpulse_sight.app.ClipboardMonitor",
                side_effect=AssertionError("watchdog must not restart a disabled monitor"),
            ):
                app._reconcile_clipboard_monitor()
        assert app._clipboard_monitor is None

    def test_watchdog_stops_a_running_monitor_when_setting_flips_off(
        self, tmp_path, monkeypatch
    ):
        # Enabled at construction, then switched off mid-session. A privacy
        # control that only takes effect on restart is not honoured.
        app = self._make_app(tmp_path, monkeypatch)
        assert app._clipboard_monitor is not None
        live = app._clipboard_monitor

        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app._reconcile_clipboard_monitor()

        assert app._clipboard_monitor is None
        assert live._stop.is_set(), "the running monitor was not told to stop"

    def test_watchdog_revives_a_dead_monitor_when_enabled(self, tmp_path, monkeypatch):
        # The reconcile must still do its original job in the enabled case.
        app = self._make_app(tmp_path, monkeypatch)
        app._clipboard_monitor = None
        app._reconcile_clipboard_monitor()
        assert app._clipboard_monitor is not None
        app._clipboard_monitor.stop()

    def test_stop_tolerates_absent_monitor(self, tmp_path, monkeypatch):
        with patch("contextpulse_sight.app.cfg_get") as cfg_get:
            cfg_get.side_effect = lambda key, default=None: (
                False if key == "clipboard_enabled" else default
            )
            app = self._make_app(tmp_path, monkeypatch)
        # Must not raise AttributeError on None.
        app._stop_clipboard_monitor()
