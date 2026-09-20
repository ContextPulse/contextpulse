# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""A memory is verbatim recall, which makes it the worst place for a secret.

The MCP surface audit found memory_store takes an arbitrary-length value with
no cap, and memory_recall / memory_search / memory_semantic_search /
memory_list hand it back in full with no redaction. Anything an agent stored --
a pasted credential, a whole config file -- is retrievable verbatim by any MCP
client (cp-voice-touch-memory-text-unredacted).

Asserts a planted secret appears in NONE of:
  1. the warm tier's `memories` table
  2. the warm tier's FTS index (probed by search, with a positive control)
  3. the hot tier (the in-process dict recall checks FIRST, so a store that
     redacted only on the way to SQLite would still serve the raw value for
     five minutes)
  4. memory_recall / memory_search / memory_semantic_search / memory_list
  5. the raw bytes of memory.db on disk

plus the 64 KB cap: refused loudly, not truncated silently.

Every value below is SYNTHETIC.
"""

import json
import sqlite3

import pytest
from contextpulse_core.redact import redact_sensitive
from contextpulse_memory import mcp_server
from contextpulse_memory.storage import MemoryStore, MemoryValueTooLarge

CONTROL_WORD = "zqcontrol"

SECRETS = [
    ("github_token", "ghp_zqmemneedle0123456789abcdefghijklmnop"),
    ("anthropic_key", "sk-ant-zqmemanthropic0123456789"),
    ("aws_access_key", "AKIAZQMEMNEEDLE01234"),
    ("password_assignment", "password: zqmempassneedle42"),
    ("ssn", "987-65-4321"),
    ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----\nzqmempem0123456789abc\n"
                    "-----END OPENSSH PRIVATE KEY-----"),
]

IDS = [s[0] for s in SECRETS]

# An FTS term drawn from inside each secret. Derived by hand rather than sliced
# off the value: the private-key fixture's last whitespace-delimited token is
# "KEY-----", whose stem "key" also appears in the replacement marker
# "[REDACTED:PRIVATE_KEY]" -- a probe that matches the redaction itself reports
# a leak that is not there.
FTS_PROBES = {
    "github_token": "zqmemneedle0123456789abcdefghijklmnop",
    "anthropic_key": "zqmemanthropic0123456789",
    "aws_access_key": "akiazqmemneedle01234",
    "password_assignment": "zqmempassneedle42",
    "ssn": "9876543210",
    "private_key": "zqmempem0123456789abc",
}


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "mem")
    yield s
    s.warm.close()
    s.cold.close()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the MCP module's singleton at a temp store."""
    s = MemoryStore(tmp_path / "mem")
    monkeypatch.setattr(mcp_server, "_store", s)
    yield s
    s.warm.close()
    s.cold.close()


class TestFixturesAreRedactable:
    @pytest.mark.parametrize("family,secret", SECRETS, ids=IDS)
    def test_pattern_removes_it(self, family, secret):
        assert secret not in redact_sensitive(secret), f"{family}: fixture is not redactable"


class TestStoreRedactsBeforeAnyTier:
    @pytest.mark.parametrize("family,secret", SECRETS, ids=IDS)
    def test_warm_tier_and_hot_tier_and_disk(self, store, tmp_path, family, secret):
        store.store(key="k/zq", value=f"{CONTROL_WORD} {secret}", tags=["zqtag"], ttl_hours=1)

        # Hot tier is checked FIRST by recall, so a fix applied only on the way
        # to SQLite would still serve the raw value for five minutes.
        hot = store.hot.get("k/zq")
        assert hot is not None, f"{family}: nothing in the hot tier -- vacuous"
        assert CONTROL_WORD in hot[0], f"{family}: hot value empty -- vacuous"
        assert secret not in hot[0], f"{family}: hot tier holds the raw value"

        recalled = store.recall("k/zq")
        assert secret not in json.dumps(recalled, default=str), f"{family}: recall leaked"

        db = tmp_path / "mem" / "memory.db"
        conn = sqlite3.connect(str(db))
        rows = [r[0] or "" for r in conn.execute("SELECT value FROM memories")]
        conn.close()
        assert rows, f"{family}: no warm row -- vacuous"
        assert CONTROL_WORD in " ".join(rows)
        assert secret not in " ".join(rows), f"{family}: warm tier holds the raw value"

        blob = db.read_bytes()
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                blob += side.read_bytes()
        assert secret.encode() not in blob, f"{family}: secret found in memory.db bytes"

    @pytest.mark.parametrize("family,secret", SECRETS, ids=IDS)
    def test_not_searchable(self, store, family, secret):
        store.store(key="k/zq", value=f"{CONTROL_WORD} {secret}", ttl_hours=1)
        # Positive control -- the FTS index is populated and reachable.
        assert store.search(CONTROL_WORD, limit=10), (
            f"{family}: control not searchable; the probe below proves nothing"
        )
        assert not store.search(FTS_PROBES[family], limit=10), (
            f"{family}: secret is searchable"
        )


class TestMcpToolsDoNotServeSecrets:
    @pytest.mark.parametrize("family,secret", SECRETS, ids=IDS)
    def test_every_read_tool(self, wired, family, secret):
        mcp_server.memory_store(key="k/zq", value=f"{CONTROL_WORD} {secret}", tags=["zqtag"])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mcp_server, "has_pro_access", lambda: True)
            responses = {
                "memory_recall": mcp_server.memory_recall("k/zq"),
                "memory_search": mcp_server.memory_search(CONTROL_WORD, mode="keyword"),
                "memory_semantic_search": mcp_server.memory_semantic_search(CONTROL_WORD),
                "memory_list": mcp_server.memory_list(),
            }

        for tool in ("memory_recall", "memory_list"):
            assert CONTROL_WORD in responses[tool], (
                f"{family}: {tool} returned nothing to redact -- assertion is vacuous"
            )
        for tool, response in responses.items():
            assert secret not in response, f"{family}: leaked through {tool}"

    @pytest.mark.parametrize("family,secret", SECRETS, ids=IDS)
    def test_a_raw_pre_fix_entry_is_scrubbed_on_read(self, wired, tmp_path, family, secret):
        """Entries written before this branch are still raw on disk."""
        wired.warm.upsert(
            key="k/raw", value=f"{CONTROL_WORD} {secret}", tags=[], expires_at=None,
        )
        out = mcp_server.memory_recall("k/raw")
        assert CONTROL_WORD in out, f"{family}: nothing returned -- vacuous"
        assert secret not in out, f"{family}: raw pre-fix entry leaked through memory_recall"

        listed = mcp_server.memory_list()
        assert secret not in listed, f"{family}: raw pre-fix entry leaked through memory_list"


class TestValueSizeCap:
    """Refused loudly, never truncated: a memory cut in half is worse than one
    that was rejected, because the caller believes it stored something."""

    def test_at_the_limit_is_accepted(self, store):
        value = "a" * MemoryStore.MAX_VALUE_BYTES
        store.store(key="k/max", value=value, ttl_hours=1)
        assert store.recall("k/max")["value"] == value

    def test_one_byte_over_is_refused(self, store):
        value = "a" * (MemoryStore.MAX_VALUE_BYTES + 1)
        with pytest.raises(MemoryValueTooLarge) as exc:
            store.store(key="k/over", value=value, ttl_hours=1)
        assert str(MemoryStore.MAX_VALUE_BYTES) in str(exc.value)
        assert store.recall("k/over") is None, "an oversized value was partially stored"

    def test_the_cap_is_bytes_not_characters(self, store):
        # A 3-byte character must count as three. len() would let a multibyte
        # value through at three times the intended size.
        value = "中" * (MemoryStore.MAX_VALUE_BYTES // 3 + 1)
        assert len(value) < MemoryStore.MAX_VALUE_BYTES
        with pytest.raises(MemoryValueTooLarge):
            store.store(key="k/wide", value=value, ttl_hours=1)

    def test_mcp_tool_reports_the_refusal_clearly(self, wired):
        out = json.loads(
            mcp_server.memory_store(key="k/over", value="a" * (MemoryStore.MAX_VALUE_BYTES + 1))
        )
        assert out["success"] is False
        assert out["reason"] == "value_too_large"
        assert "64 KB" in out["error"], f"error does not name the limit: {out['error']!r}"

    def test_an_ordinary_value_is_still_stored(self, wired):
        # The cap must not disable the tool it guards.
        out = json.loads(mcp_server.memory_store(key="k/ok", value=f"{CONTROL_WORD} note"))
        assert out["success"] is True
        assert CONTROL_WORD in mcp_server.memory_recall("k/ok")
