# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Cross-source content matcher — derives sync offsets from per-source transcripts.

Algorithm (Phase 1 step 1.3 of the voice-pipeline architecture):
  1. Extract distinctive n-grams (5-7 words, normalized) from each transcript
  2. For each pair of sources, find n-grams appearing in both
  3. Compute time deltas at each shared anchor; require >= MIN_ANCHORS
     deltas agreeing within AGREEMENT_EPS to accept a pair offset
  4. Build a sync graph (nodes = sources, edges = reliable pair offsets)
  5. Pick anchor source (earliest DJI BWF UTC) and propagate timeline via BFS

Output: every reachable source gets wall_start_utc filled in, anchored to
the BWF-known source's UTC. Sources not reachable through the graph are
flagged as unreachable.

This is the COARSE alignment step (~200-500ms precision, limited by
Whisper segment.start jitter). Sub-millisecond refinement via audio
cross-correlation is a separate downstream step (A.3b).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection

logger = logging.getLogger(__name__)

# Algorithm tuning
DEFAULT_NGRAM_MIN = 5
DEFAULT_NGRAM_MAX = 7
DEFAULT_MIN_ANCHORS = 3
DEFAULT_AGREEMENT_EPS_SEC = 2.0
DEFAULT_IDF_MAX_FRACTION = 0.5  # n-gram occurring in > 50% of sources = not distinctive
DEFAULT_MIN_WORD_CHARS = 3  # filter "to the an" filler

_WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class Anchor:
    """One occurrence of an n-gram in a transcript."""

    source_sha256: str
    ngram: str
    start_sec: float  # segment.start (relative to source t=0)


@dataclass(frozen=True)
class AnchorPair:
    """A shared anchor between two sources with both timestamps."""

    ngram: str
    source_a: str
    start_a_sec: float
    source_b: str
    start_b_sec: float

    @property
    def delta_sec(self) -> float:
        """time_in_B - time_in_A. Positive = B is ahead of A on its own clock."""
        return self.start_b_sec - self.start_a_sec


@dataclass(frozen=True)
class PairOffset:
    """Computed sync offset between two sources, with confidence."""

    source_a: str
    source_b: str
    offset_sec: float  # B_t0 - A_t0; add this to A's timeline to get B's
    n_anchors: int
    std_dev_sec: float


@dataclass
class ResolvedSource:
    """A source whose wall_start_utc has been determined."""

    sha256: str
    wall_start_utc: datetime
    provenance: str  # "bwf" | "matched"
    anchor_count: int = 0
    offset_from_anchor_sec: float = 0.0


@dataclass
class SyncResult:
    """Output of resolve_timeline()."""

    container: str
    anchor_source_sha256: str
    anchor_origination_utc: datetime
    resolved_sources: list[ResolvedSource] = field(default_factory=list)
    unreachable_sources: list[str] = field(default_factory=list)
    pair_offsets: list[PairOffset] = field(default_factory=list)

    def to_json(self, *, path: Path | None = None) -> str:
        payload = {
            "container": self.container,
            "anchor_source_sha256": self.anchor_source_sha256,
            "anchor_origination_utc": self.anchor_origination_utc.isoformat(),
            "resolved_sources": [
                {
                    "sha256": r.sha256,
                    "wall_start_utc": r.wall_start_utc.isoformat(),
                    "provenance": r.provenance,
                    "anchor_count": r.anchor_count,
                    "offset_from_anchor_sec": r.offset_from_anchor_sec,
                }
                for r in self.resolved_sources
            ],
            "unreachable_sources": self.unreachable_sources,
            "pair_offsets": [
                {
                    "source_a": p.source_a,
                    "source_b": p.source_b,
                    "offset_sec": p.offset_sec,
                    "n_anchors": p.n_anchors,
                    "std_dev_sec": p.std_dev_sec,
                }
                for p in self.pair_offsets
            ],
        }
        text = json.dumps(payload, indent=2, default=str)
        if path is not None:
            path.write_text(text, encoding="utf-8")
        return text


# ---------------------------------------------------------------------------
# n-gram extraction
# ---------------------------------------------------------------------------


def _normalize_words(text: str, min_word_chars: int = DEFAULT_MIN_WORD_CHARS) -> list[str]:
    """Lowercase, strip punctuation, drop short filler words."""
    words = _WORD_RE.findall(text.lower())
    return [w for w in words if len(w) >= min_word_chars]


def extract_ngrams(
    transcript: dict,
    *,
    n_min: int = DEFAULT_NGRAM_MIN,
    n_max: int = DEFAULT_NGRAM_MAX,
) -> dict[str, list[Anchor]]:
    """Build a {ngram_text → [Anchor]} index from one transcript dict.

    Anchor.start_sec is the segment.start of the segment containing the n-gram's
    first word. Caller is responsible for picking the right transcript schema
    (we expect the verbose Whisper format with `segments[].text` and
    `segments[].start`).
    """
    sha = transcript.get("source_sha256") or transcript.get("session_id", "")
    index: dict[str, list[Anchor]] = defaultdict(list)
    for seg in transcript.get("segments", []):
        seg_text = seg.get("text", "")
        seg_start = float(seg.get("start", 0.0))
        words = _normalize_words(seg_text)
        for n in range(n_min, n_max + 1):
            for i in range(len(words) - n + 1):
                ngram = " ".join(words[i : i + n])
                index[ngram].append(Anchor(source_sha256=sha, ngram=ngram, start_sec=seg_start))
    return index


# ---------------------------------------------------------------------------
# Pair anchor finding + offset computation
# ---------------------------------------------------------------------------


def find_pair_anchors(
    index_a: dict[str, list[Anchor]],
    index_b: dict[str, list[Anchor]],
    *,
    excluded_ngrams: set[str] | None = None,
) -> list[AnchorPair]:
    """Find n-grams present in both indexes; emit one AnchorPair per shared occurrence pair.

    If an n-gram occurs M times in A and N times in B, we emit M*N pairs (cross product).
    The offset clusterer downstream filters out the wrong ones.
    """
    excluded = excluded_ngrams or set()
    shared = (set(index_a.keys()) & set(index_b.keys())) - excluded
    pairs: list[AnchorPair] = []
    for ngram in shared:
        for a in index_a[ngram]:
            for b in index_b[ngram]:
                if a.source_sha256 == b.source_sha256:
                    continue
                pairs.append(
                    AnchorPair(
                        ngram=ngram,
                        source_a=a.source_sha256,
                        start_a_sec=a.start_sec,
                        source_b=b.source_sha256,
                        start_b_sec=b.start_sec,
                    )
                )
    return pairs


def compute_pair_offset(
    anchors: list[AnchorPair],
    *,
    min_count: int = DEFAULT_MIN_ANCHORS,
    agreement_eps_sec: float = DEFAULT_AGREEMENT_EPS_SEC,
) -> PairOffset | None:
    """Cluster anchor deltas; return offset only if min_count agree within eps.

    Algorithm: take median delta as candidate offset, count how many anchors
    fall within +/- eps of it. If >= min_count, return PairOffset with that
    median + std-dev of agreeing subset.
    """
    if len(anchors) < min_count:
        return None

    # All pairs must be from the same (a, b) source pair
    src_a_set = {p.source_a for p in anchors}
    src_b_set = {p.source_b for p in anchors}
    if len(src_a_set) != 1 or len(src_b_set) != 1:
        raise ValueError("compute_pair_offset expects anchors from a single source pair")

    deltas = [p.delta_sec for p in anchors]
    median = statistics.median(deltas)
    agreeing = [d for d in deltas if abs(d - median) <= agreement_eps_sec]
    if len(agreeing) < min_count:
        return None

    offset = statistics.median(agreeing)
    std_dev = statistics.pstdev(agreeing) if len(agreeing) > 1 else 0.0
    return PairOffset(
        source_a=anchors[0].source_a,
        source_b=anchors[0].source_b,
        offset_sec=offset,
        n_anchors=len(agreeing),
        std_dev_sec=std_dev,
    )


# ---------------------------------------------------------------------------
# Corpus-wide IDF filter
# ---------------------------------------------------------------------------


def common_ngrams(
    indexes: Iterable[dict[str, list[Anchor]]],
    *,
    max_fraction: float = DEFAULT_IDF_MAX_FRACTION,
) -> set[str]:
    """N-grams appearing in more than `max_fraction` of source indexes are 'common'."""
    indexes_list = list(indexes)
    n_sources = len(indexes_list)
    if n_sources == 0:
        return set()
    counts: dict[str, int] = defaultdict(int)
    for idx in indexes_list:
        for ngram in idx:
            counts[ngram] += 1
    threshold = max_fraction * n_sources
    return {ng for ng, c in counts.items() if c > threshold}


# ---------------------------------------------------------------------------
# Graph propagation
# ---------------------------------------------------------------------------


def _build_offset_map(offsets: list[PairOffset]) -> dict[str, dict[str, float]]:
    """Adjacency map: src → {neighbor → wall_clock_offset (neighbor.wall_start - src.wall_start)}.

    PairOffset.offset_sec is `delta = B.t - A.t` on transcript clocks. The relationship
    between wall-clocks and transcript clocks for a shared phrase at wall-time T:
      A.t = T - A.wall_start
      B.t = T - B.wall_start
      delta = B.t - A.t = A.wall_start - B.wall_start = -(B.wall_start - A.wall_start)
    So the wall-clock edge weight is -offset_sec.
    """
    g: dict[str, dict[str, float]] = defaultdict(dict)
    for p in offsets:
        wall_offset = -p.offset_sec  # B.wall_start - A.wall_start
        g[p.source_a][p.source_b] = wall_offset
        g[p.source_b][p.source_a] = -wall_offset
    return g


def _propagate_from_anchor(
    graph: dict[str, dict[str, float]],
    anchor_sha: str,
) -> dict[str, float]:
    """BFS from anchor; return {sha → offset_from_anchor_sec}.

    offset_from_anchor[X] means: X's t=0 occurs offset seconds AFTER anchor's t=0.
    """
    offsets = {anchor_sha: 0.0}
    queue: deque[str] = deque([anchor_sha])
    while queue:
        cur = queue.popleft()
        for neighbor, edge_offset in graph[cur].items():
            if neighbor in offsets:
                continue
            offsets[neighbor] = offsets[cur] + edge_offset
            queue.append(neighbor)
    return offsets


# ---------------------------------------------------------------------------
# End-to-end resolve_timeline
# ---------------------------------------------------------------------------


def _load_transcript(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_anchor_source(coll: RawSourceCollection) -> RawSource | None:
    """Pick the source with the earliest BWF (or filename) UTC as the timeline anchor."""
    candidates = [s for s in coll.sources if s.bwf_origination is not None]
    if candidates:
        return min(candidates, key=lambda s: s.bwf_origination)  # type: ignore[arg-type,return-value]
    candidates = [s for s in coll.sources if s.filename_origination is not None]
    if candidates:
        return min(candidates, key=lambda s: s.filename_origination)  # type: ignore[arg-type,return-value]
    return None


def resolve_timeline(
    coll: RawSourceCollection,
    transcripts_dir: Path,
    *,
    n_min: int = DEFAULT_NGRAM_MIN,
    n_max: int = DEFAULT_NGRAM_MAX,
    min_anchors: int = DEFAULT_MIN_ANCHORS,
    agreement_eps_sec: float = DEFAULT_AGREEMENT_EPS_SEC,
    idf_max_fraction: float = DEFAULT_IDF_MAX_FRACTION,
) -> SyncResult:
    """End-to-end: read per-source transcripts, derive timeline, return SyncResult.

    transcripts_dir layout: {sha256[:16]}.json per source (matches A.2 output).
    """
    anchor = _pick_anchor_source(coll)
    if anchor is None:
        raise ValueError("Cannot resolve timeline: no source has a BWF or filename origination.")
    anchor_utc = anchor.best_origination_utc
    assert anchor_utc is not None

    # Load all transcripts and build n-gram indexes
    indexes: dict[str, dict[str, list[Anchor]]] = {}
    for rs in coll.sources:
        json_path = transcripts_dir / f"{rs.sha256[:16]}.json"
        if not json_path.exists():
            logger.warning("No transcript for %s at %s", rs.file_path, json_path)
            continue
        transcript = _load_transcript(json_path)
        # Override session_id->source_sha256 in case schema differs
        transcript["source_sha256"] = rs.sha256
        indexes[rs.sha256] = extract_ngrams(transcript, n_min=n_min, n_max=n_max)

    # IDF filter
    excluded = common_ngrams(indexes.values(), max_fraction=idf_max_fraction)
    logger.info("Filtered %d common n-grams via IDF", len(excluded))

    # Compute all pairwise offsets
    pair_offsets: list[PairOffset] = []
    sources = list(indexes.keys())
    for i, sha_a in enumerate(sources):
        for sha_b in sources[i + 1 :]:
            anchors_pair = find_pair_anchors(
                indexes[sha_a], indexes[sha_b], excluded_ngrams=excluded
            )
            offset = compute_pair_offset(
                anchors_pair, min_count=min_anchors, agreement_eps_sec=agreement_eps_sec
            )
            if offset is not None:
                pair_offsets.append(offset)
                logger.info(
                    "Offset %s..↔%s..: %.2fs (n=%d, std=%.2f)",
                    sha_a[:8],
                    sha_b[:8],
                    offset.offset_sec,
                    offset.n_anchors,
                    offset.std_dev_sec,
                )

    # Build graph + propagate from anchor
    graph = _build_offset_map(pair_offsets)
    propagated = _propagate_from_anchor(graph, anchor.sha256)

    # Build SyncResult
    result = SyncResult(
        container=coll.container,
        anchor_source_sha256=anchor.sha256,
        anchor_origination_utc=anchor_utc,
        pair_offsets=pair_offsets,
    )
    for rs in coll.sources:
        if rs.bwf_origination is not None:
            # Trust BWF directly; offset_from_anchor is BWF-derived
            offset_sec = (rs.bwf_origination - anchor_utc).total_seconds()
            result.resolved_sources.append(
                ResolvedSource(
                    sha256=rs.sha256,
                    wall_start_utc=rs.bwf_origination,
                    provenance="bwf",
                    anchor_count=0,
                    offset_from_anchor_sec=offset_sec,
                )
            )
        elif rs.sha256 in propagated:
            offset_sec = propagated[rs.sha256]
            wall_start = anchor_utc + timedelta(seconds=offset_sec)
            # Count how many pair_offsets touch this source
            anchor_count = sum(
                p.n_anchors
                for p in pair_offsets
                if p.source_a == rs.sha256 or p.source_b == rs.sha256
            )
            result.resolved_sources.append(
                ResolvedSource(
                    sha256=rs.sha256,
                    wall_start_utc=wall_start,
                    provenance="matched",
                    anchor_count=anchor_count,
                    offset_from_anchor_sec=offset_sec,
                )
            )
        else:
            result.unreachable_sources.append(rs.sha256)

    return result
