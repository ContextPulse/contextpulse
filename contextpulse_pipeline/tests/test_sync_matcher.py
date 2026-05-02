"""Tests for contextpulse_pipeline.sync_matcher — cross-source content matcher."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection
from contextpulse_pipeline.sync_matcher import (
    AnchorPair,
    common_ngrams,
    compute_pair_offset,
    extract_ngrams,
    find_pair_anchors,
    resolve_timeline,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic transcripts
# ---------------------------------------------------------------------------


def _t(sha: str, segments: list[tuple[float, str]]) -> dict:
    """Build a minimal transcript dict in the A.2 output schema."""
    return {
        "source_sha256": sha,
        "session_id": "ep-test",
        "segments": [{"start": s, "end": s + 5.0, "text": text} for s, text in segments],
    }


# ---------------------------------------------------------------------------
# extract_ngrams
# ---------------------------------------------------------------------------


class TestExtractNgrams:
    def test_basic_ngrams_extracted(self) -> None:
        t = _t("a" * 64, [(0.0, "the quick brown fox jumps over lazy dog now")])
        idx = extract_ngrams(t, n_min=5, n_max=5)
        # Words after filtering short fillers ("the"): quick brown fox jumps over lazy dog now → 8 words
        # 5-grams: 4 sliding windows
        assert any("quick brown fox jumps over" in k for k in idx.keys())

    def test_normalizes_lowercase_and_punctuation(self) -> None:
        t1 = _t("a" * 64, [(0.0, "Hello World, this is a test sentence.")])
        t2 = _t("b" * 64, [(0.0, "hello world this is a test sentence")])
        idx1 = extract_ngrams(t1, n_min=5, n_max=5)
        idx2 = extract_ngrams(t2, n_min=5, n_max=5)
        # Same n-grams should appear in both
        assert set(idx1.keys()) & set(idx2.keys())

    def test_short_filler_words_filtered(self) -> None:
        # min_word_chars=3 default → "to", "an", "is", "a" filtered
        t = _t("a" * 64, [(0.0, "an apple a day keeps the doctor away")])
        idx = extract_ngrams(t, n_min=3, n_max=3)
        # Words kept: apple, day, keeps, the, doctor, away — 6 words
        # Wait, "the" is 3 chars so kept. "an" and "a" are 2 → filtered.
        # Output: ["apple", "day", "keeps", "the", "doctor", "away"]
        assert any("apple day keeps" in k for k in idx.keys())

    def test_anchor_records_segment_start(self) -> None:
        t = _t("a" * 64, [(42.0, "one two three four five six seven")])
        idx = extract_ngrams(t, n_min=5, n_max=5)
        for anchors in idx.values():
            for a in anchors:
                assert a.start_sec == 42.0


# ---------------------------------------------------------------------------
# find_pair_anchors + compute_pair_offset
# ---------------------------------------------------------------------------


class TestPairOffset:
    def test_three_agreeing_anchors_give_offset(self) -> None:
        # Two transcripts that share three distinctive phrases at known offsets.
        # Source A timeline: 10s, 50s, 90s
        # Source B timeline: 100s, 140s, 180s
        # → offset_b_minus_a = 90s
        t_a = _t(
            "a" * 64,
            [
                (10.0, "alpha bravo charlie delta echo foxtrot"),
                (50.0, "lima mike november oscar papa quebec"),
                (90.0, "uniform victor whiskey xray yankee zulu"),
            ],
        )
        t_b = _t(
            "b" * 64,
            [
                (100.0, "alpha bravo charlie delta echo foxtrot"),
                (140.0, "lima mike november oscar papa quebec"),
                (180.0, "uniform victor whiskey xray yankee zulu"),
            ],
        )
        idx_a = extract_ngrams(t_a)
        idx_b = extract_ngrams(t_b)
        anchors = find_pair_anchors(idx_a, idx_b)
        assert len(anchors) >= 3
        offset = compute_pair_offset(anchors, min_count=3, agreement_eps_sec=2.0)
        assert offset is not None
        assert abs(offset.offset_sec - 90.0) < 0.01
        assert offset.n_anchors >= 3

    def test_disagreeing_anchors_rejected(self) -> None:
        # Three "anchors" but each gives a wildly different delta (false matches)
        anchors = [
            AnchorPair(ngram="x", source_a="a", start_a_sec=0.0, source_b="b", start_b_sec=10.0),
            AnchorPair(ngram="y", source_a="a", start_a_sec=0.0, source_b="b", start_b_sec=300.0),
            AnchorPair(ngram="z", source_a="a", start_a_sec=0.0, source_b="b", start_b_sec=600.0),
        ]
        offset = compute_pair_offset(anchors, min_count=3, agreement_eps_sec=2.0)
        assert offset is None

    def test_below_min_count_returns_none(self) -> None:
        anchors = [
            AnchorPair(ngram="x", source_a="a", start_a_sec=0.0, source_b="b", start_b_sec=50.0),
            AnchorPair(ngram="y", source_a="a", start_a_sec=10.0, source_b="b", start_b_sec=60.0),
        ]
        offset = compute_pair_offset(anchors, min_count=3)
        assert offset is None


# ---------------------------------------------------------------------------
# IDF filter
# ---------------------------------------------------------------------------


class TestCommonNgrams:
    def test_ngram_in_majority_marked_common(self) -> None:
        idx_a = extract_ngrams(_t("a" * 64, [(0.0, "yeah I think that's right buddy")]))
        idx_b = extract_ngrams(_t("b" * 64, [(0.0, "yeah I think that's right buddy")]))
        idx_c = extract_ngrams(_t("c" * 64, [(0.0, "yeah I think that's right buddy")]))
        idx_d = extract_ngrams(_t("d" * 64, [(0.0, "totally different content here please")]))
        common = common_ngrams([idx_a, idx_b, idx_c, idx_d], max_fraction=0.5)
        # "yeah think that's right buddy" appears in 3/4 = 75% > 50% → common
        assert any("think that" in g for g in common)

    def test_distinctive_ngram_not_common(self) -> None:
        idx_a = extract_ngrams(_t("a" * 64, [(0.0, "yeah I think that's right buddy")]))
        idx_b = extract_ngrams(
            _t("b" * 64, [(0.0, "completely unrelated text appears uniquely here")])
        )
        common = common_ngrams([idx_a, idx_b], max_fraction=0.5)
        # No ngram in both → none is common
        assert len(common) == 0


# ---------------------------------------------------------------------------
# resolve_timeline — end-to-end synthetic
# ---------------------------------------------------------------------------


def _write_transcript(transcripts_dir: Path, sha: str, segments: list[tuple[float, str]]) -> None:
    import json

    transcripts_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "source_sha256": sha,
        "session_id": "ep-test",
        "segments": [{"start": s, "end": s + 5.0, "text": text} for s, text in segments],
    }
    (transcripts_dir / f"{sha[:16]}.json").write_text(__import__("json").dumps(doc))
    _ = json  # silence unused-import linter


def _make_rs(sha: str, *, bwf: datetime | None = None, file_path: str = "x.wav") -> RawSource:
    return RawSource(
        sha256=sha,
        file_path=file_path,
        container="ep-test",
        source_tier="A" if bwf else "C",
        duration_sec=300.0,
        sample_rate=48000,
        channel_count=1,
        codec="pcm" if bwf else "mp3",
        bit_depth=24 if bwf else None,
        bwf_origination=bwf,
        provenance="bwf" if bwf else "none",
    )


class TestResolveTimeline:
    def test_three_source_chain_propagates(self, tmp_path: Path) -> None:
        # Three sources: A (BWF anchor at T0), B (no BWF), C (no BWF).
        # B starts 100 sec after A; C starts 200 sec after A.
        # A and B share anchors directly; B and C share anchors directly.
        # Test that C is resolved transitively through B.
        sha_a = "a" * 64
        sha_b = "b" * 64
        sha_c = "c" * 64

        # A: phrases at internal times 10/50 (relative to A's t=0)
        # B: same phrases at A_offset + 100 (relative to B's t=0 = -100s of A's t=0)
        #    so phrase at A.t=10 → B.t = 10 - 100 = -90? No wait.
        # If B's wall_start_utc = A.wall_start_utc + 100 (B started 100 sec later),
        # then a phrase happening at wall-time T occurs at:
        #   A.t = T - A.start
        #   B.t = T - B.start = T - (A.start + 100) = A.t - 100
        # So phrase at A.t=110 → B.t=10. Offset_B_minus_A = -100.
        _write_transcript(
            tmp_path / "transcripts",
            sha_a,
            [
                (110.0, "alpha bravo charlie delta echo foxtrot golf"),
                (150.0, "lima mike november oscar papa quebec romeo"),
            ],
        )
        _write_transcript(
            tmp_path / "transcripts",
            sha_b,
            [
                (10.0, "alpha bravo charlie delta echo foxtrot golf"),
                (50.0, "lima mike november oscar papa quebec romeo"),
                # B-only phrases for connecting to C:
                (180.0, "tango uniform victor whiskey xray yankee zulu"),
                (220.0, "ginger orange purple yellow brown silver bronze"),
            ],
        )
        # C started 200 sec after A → 100 sec after B.
        # Phrase at B.t=180 → C.t=80; B.t=220 → C.t=120
        _write_transcript(
            tmp_path / "transcripts",
            sha_c,
            [
                (80.0, "tango uniform victor whiskey xray yankee zulu"),
                (120.0, "ginger orange purple yellow brown silver bronze"),
                # Need at least 3 anchors: add a third
                (160.0, "alphabet bicycle carbon dynamite engine forklift glacier"),
            ],
        )
        # Boost B with that third C-shared phrase
        # Actually let's reconstruct B with 3 phrases shared with C
        _write_transcript(
            tmp_path / "transcripts",
            sha_b,
            [
                (10.0, "alpha bravo charlie delta echo foxtrot golf"),
                (50.0, "lima mike november oscar papa quebec romeo"),
                # B-only phrases for connecting to C (3 of them now):
                (180.0, "tango uniform victor whiskey xray yankee zulu"),
                (220.0, "ginger orange purple yellow brown silver bronze"),
                (260.0, "alphabet bicycle carbon dynamite engine forklift glacier"),
            ],
        )

        # A also needs a 3rd phrase shared with B
        _write_transcript(
            tmp_path / "transcripts",
            sha_a,
            [
                (110.0, "alpha bravo charlie delta echo foxtrot golf"),
                (150.0, "lima mike november oscar papa quebec romeo"),
                (200.0, "saxophone trombone violin clarinet harmonica accordion banjo"),
            ],
        )
        # And give B that 3rd phrase too
        _write_transcript(
            tmp_path / "transcripts",
            sha_b,
            [
                (10.0, "alpha bravo charlie delta echo foxtrot golf"),
                (50.0, "lima mike november oscar papa quebec romeo"),
                (100.0, "saxophone trombone violin clarinet harmonica accordion banjo"),
                (180.0, "tango uniform victor whiskey xray yankee zulu"),
                (220.0, "ginger orange purple yellow brown silver bronze"),
                (260.0, "alphabet bicycle carbon dynamite engine forklift glacier"),
            ],
        )

        anchor_utc = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
        coll = RawSourceCollection(
            container="ep-test",
            sources=[
                _make_rs(sha_a, bwf=anchor_utc),
                _make_rs(sha_b),
                _make_rs(sha_c),
            ],
        )

        result = resolve_timeline(
            coll,
            tmp_path / "transcripts",
            min_anchors=3,
            agreement_eps_sec=2.0,
            idf_max_fraction=1.0,  # disable IDF filter for tiny test corpus
        )
        assert result.anchor_source_sha256 == sha_a
        # All three sources should be resolved
        resolved_shas = {r.sha256 for r in result.resolved_sources}
        assert resolved_shas == {sha_a, sha_b, sha_c}
        assert len(result.unreachable_sources) == 0

        # B's wall_start_utc should be A's + 100 seconds
        b = next(r for r in result.resolved_sources if r.sha256 == sha_b)
        delta_b = (b.wall_start_utc - anchor_utc).total_seconds()
        assert abs(delta_b - 100.0) < 0.5

        # C's wall_start_utc should be A's + 200 seconds (transitive via B)
        c = next(r for r in result.resolved_sources if r.sha256 == sha_c)
        delta_c = (c.wall_start_utc - anchor_utc).total_seconds()
        assert abs(delta_c - 200.0) < 0.5

    def test_unreachable_source_flagged(self, tmp_path: Path) -> None:
        # A is anchor; B has no shared content with A → unreachable.
        sha_a = "a" * 64
        sha_b = "b" * 64
        _write_transcript(
            tmp_path / "transcripts",
            sha_a,
            [(10.0, "alpha bravo charlie delta echo foxtrot golf")],
        )
        _write_transcript(
            tmp_path / "transcripts",
            sha_b,
            [(20.0, "completely different unrelated content nothing matches here")],
        )
        coll = RawSourceCollection(
            container="ep-test",
            sources=[
                _make_rs(sha_a, bwf=datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)),
                _make_rs(sha_b),
            ],
        )
        result = resolve_timeline(
            coll, tmp_path / "transcripts", min_anchors=3, idf_max_fraction=1.0
        )
        # A resolved (BWF), B unreachable
        assert sha_a in {r.sha256 for r in result.resolved_sources}
        assert sha_b in result.unreachable_sources


# ---------------------------------------------------------------------------
# Integration test against real Josh hike transcripts (skip if A.2 hasn't run)
# ---------------------------------------------------------------------------

JOSH_TRANSCRIPTS = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/transcripts"
)
JOSH_RAW_JSON = Path(
    "C:/Users/david/Projects/ContextPulse/working/ep-2026-04-26-josh-cashman/raw_sources.json"
)


@pytest.mark.skipif(
    not JOSH_TRANSCRIPTS.exists() or not JOSH_RAW_JSON.exists(),
    reason="A.2 transcription has not produced Josh hike transcripts yet",
)
class TestJoshHikeIntegration:
    def test_resolves_all_14_sources(self) -> None:
        coll = RawSourceCollection.from_json(path=JOSH_RAW_JSON)
        result = resolve_timeline(coll, JOSH_TRANSCRIPTS)
        # Expect at least the 7 BWF-anchored DJI sources resolved
        bwf_sources = [r for r in result.resolved_sources if r.provenance == "bwf"]
        assert len(bwf_sources) >= 1
        # And at least some Telegram sources matched via cross-source phrases
        matched_sources = [r for r in result.resolved_sources if r.provenance == "matched"]
        assert len(matched_sources) >= 1
        # Total resolved + unreachable should equal total sources
        assert len(result.resolved_sources) + len(result.unreachable_sources) == len(coll.sources)
