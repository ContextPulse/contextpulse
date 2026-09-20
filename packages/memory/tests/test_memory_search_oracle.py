# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""memory_search must not leak a stored secret through its result count.

Review S-1. `WarmTier.search` ran `memories_fts MATCH ?` over the raw stored
`value`, and the MCP tool returned `{"count": len(results), "results":
_scrub(results)}` -- output redacted, count computed over raw text.
`ColdTier.search` had the identical shape over `cold_summaries.text_content`.
That is the same oracle the clipboard case was fixed for: a client issues
"sk-", "sk-a", "sk-ab" ... and reads the count while every value it is shown is
correctly `[REDACTED:...]`.

Both indexes are declared ``tokenize='porter unicode61'``, so a literal
substring filter would not have closed it either (review S-4).

Entries are planted RAW -- straight through `WarmTier.upsert` and
`ColdTier.ingest`, bypassing `MemoryStore.store` -- because pre-fix rows are
the population an attacker would probe, and a store that is already clean
cannot test this at all.

Every value below is SYNTHETIC.
"""

import json
import string
import time

import pytest
from contextpulse_memory import mcp_server
from contextpulse_memory.storage import MemoryStore

CONTROL_WORD = "zqcontrol"
SECRET = "sk-zqmemoracleneedle0123456789ABCDEFGH"
ALPHABET = string.ascii_lowercase + string.digits + "-_"


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "mem")
    yield s
    s.warm.close()
    s.cold.close()


@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(mcp_server, "_store", store)
    monkeypatch.setattr(mcp_server, "has_pro_access", lambda: True)
    return store


def _plant_warm(store):
    store.warm.upsert(
        key="notes/deploy", value=f"{CONTROL_WORD} {SECRET}", tags=[], expires_at=None,
    )


def _plant_cold(store):
    store.cold.ingest([
        {
            "key": f"{CONTROL_WORD}-archived",
            "value": f"{CONTROL_WORD} {SECRET}",
            "updated_at": time.time(),
            "modality": "memory",
        }
    ])


def _extract_by_counting(probe_fn, max_len, seed=""):
    """Extend a prefix one character at a time, keeping whatever still returns
    a non-zero count. `seed` models an attacker who knows the token prefix --
    which is always true, since every secret family has a fixed one."""
    recovered = seed
    for _ in range(max_len):
        for ch in ALPHABET:
            if probe_fn(recovered + ch) > 0:
                recovered += ch
                break
        else:
            break
    return recovered


class TestTheFixtureIsRedactable:
    def test_the_planted_secret_really_is_removed(self):
        from contextpulse_core.redact import redact_sensitive

        assert SECRET not in redact_sensitive(f"{CONTROL_WORD} {SECRET}")


class TestWarmTierSearchIsNotAnOracle:
    @pytest.mark.parametrize(
        "probe",
        [
            "zqmemoracleneedle*",
            "zqmemoracleneedles*",   # the porter bypass
            "zqmemoracleneedle0123456789ABCDEFGH",
        ],
    )
    def test_a_probe_for_the_secret_returns_nothing(self, store, probe):
        _plant_warm(store)
        # Positive control on the same index: the row IS there and IS findable
        # by its benign content, so a zero below is redaction, not an empty DB.
        assert len(store.warm.search(CONTROL_WORD)) == 1
        assert store.warm.search(probe) == []

    def test_ordinary_search_still_works(self, store):
        store.warm.upsert(
            key="notes/plain", value="running the importer", tags=["ops"], expires_at=None,
        )
        assert len(store.warm.search("run")) == 1, "the filter broke porter stemming"
        assert len(store.warm.search("importer")) == 1


class TestColdTierSearchIsNotAnOracle:
    @pytest.mark.parametrize("probe", ["zqmemoracleneedle*", "zqmemoracleneedles*"])
    def test_a_probe_for_the_secret_returns_nothing(self, store, probe):
        _plant_cold(store)
        assert len(store.cold.search(CONTROL_WORD)) == 1
        assert store.cold.search(probe) == []


class TestMemorySearchToolCountIsOverRedactedText:
    def test_the_count_field_does_not_confirm_a_secret(self, wired):
        _plant_warm(wired)
        hit = json.loads(mcp_server.memory_search(CONTROL_WORD, mode="keyword"))
        miss = json.loads(mcp_server.memory_search("zqmemoracleneedle*", mode="keyword"))

        assert hit["count"] == 1, f"control did not match: {hit}"
        assert SECRET not in json.dumps(hit)
        assert miss["count"] == 0, (
            "the count is still computed over raw stored text"
        )

    def test_a_character_by_character_walk_recovers_nothing(self, wired):
        _plant_warm(wired)

        def probe(prefix):
            return json.loads(
                mcp_server.memory_search(prefix + "*", limit=50, mode="keyword")
            )["count"]

        seeded = _extract_by_counting(probe, max_len=len(SECRET) + 5, seed="zqmemoracl")
        assert seeded == "zqmemoracl", (
            f"the count oracle extended a known prefix into the secret: {seeded!r}"
        )

    def test_the_cold_tier_fallback_is_filtered_too(self, wired):
        """MemoryStore.search tops up from the cold tier when warm is short."""
        _plant_cold(wired)
        assert len(wired.search(CONTROL_WORD)) == 1
        assert wired.search("zqmemoracleneedle*") == []
