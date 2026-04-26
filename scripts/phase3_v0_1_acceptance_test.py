"""Phase 3 v0.1 Acceptance Test for contextpulse_pipeline.

Two tests:
  Test 1 (regression): synthesis-only on existing josh-cashman transcripts.
    Uses Manifest + synthesize() directly (public APIs). No Groq cost.
    Compares outputs to baseline at outputs/ep-2026-04-26-josh-cashman/.

  Test 2 (fresh e2e): full pipeline from raw DJI WAVs for ep-2026-04-19.
    Uses BatchPipeline.ingest() + .finalize(). Auto-compresses + Groq-transcribes.

Usage:
    .venv/Scripts/python.exe scripts/phase3_v0_1_acceptance_test.py
    # Or with --skip-test2 to run just the cheap regression:
    .venv/Scripts/python.exe scripts/phase3_v0_1_acceptance_test.py --skip-test2

Env required: ANTHROPIC_API_KEY, GROQ_API_KEY (only for Test 2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

from contextpulse_pipeline import BatchPipeline, Manifest
from contextpulse_pipeline.manifest import AudioEntry
from contextpulse_pipeline.synthesize import synthesize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase3_v0_1")

BUCKET = "jerard-activefounder"
REGION = "us-east-1"

TEST1_CONTAINER = "ep-2026-04-26-josh-cashman-v0_1-test"
BASELINE_CONTAINER = "ep-2026-04-26-josh-cashman"
TEST1_TRANSCRIPT_PREFIX = "transcripts/ep-2026-04-26-josh-cashman/dji/"

TEST2_CONTAINER = "ep-2026-04-19-conflation-cleanup"
TEST2_WAV_DIR = Path(
    r"C:\Users\david\Projects\ActiveFounder\local-archive\dji-mic3\drive-D"
    r"\TX_MIC001_20260418_185414"
)
TEST2_WAVS = [
    "TX01_MIC011_20260419_060132_orig.wav",
    "TX01_MIC012_20260419_063133_orig.wav",
    "TX01_MIC013_20260419_070133_orig.wav",
    "TX01_MIC014_20260419_073133_orig.wav",
    "TX01_MIC015_20260419_080133_orig.wav",
    "TX01_MIC016_20260419_083133_orig.wav",
    "TX01_MIC017_20260419_090133_orig.wav",
]

# Production AF prompts (verbatim from Lambda handlers).
EVAL_SYSTEM = """You are evaluating an EARLY-STAGE brainstorm — not a pitch, not a plan. Two founders (David: tech, AI ops; Chris: brand, CBO at Outside Inc.) are hiking and thinking out loud. Treat this as a seed, not a business plan. Your job is to grade the IDEA'S POTENTIAL if it were pursued, not to judge whether they've figured everything out yet.

Grading rubric (1-5 each):
1. Market Size — would this matter to >100K people?
2. Execution Feasibility — could these founders ship this?
3. Differentiation — defensible / distinctive?
4. Revenue Potential — plausible path to money?
5. Founder Fit — combined strengths (tech + outdoor brand + AI ops)?

Weighted: Market 20%, Execution 25%, Differentiation 25%, Revenue 20%, Founder Fit 10%.
Verdict thresholds: 4.0+ STRONG GO, 3.3-4.0 GO, 2.5-3.3 CONDITIONAL GO, <2.5 PASS.

Return JSON:
{"weighted_score": float, "verdict": "STRONG GO|GO|CONDITIONAL GO|PASS",
 "scores": {"market_size":{"score":n,"rationale":"..."}, ...},
 "top_risks": [up to 3], "top_opportunities": [up to 3],
 "core_idea_summary": "one sentence"}

CRITICAL: NEVER NAME REAL PEOPLE other than David or Chris.
Output ONLY the JSON."""

ACTIONS_SYSTEM = """Extract action items from a founder conversation transcript (David and Chris).

Return JSON only:
{"items":[{"id":"act-001","type":"research|task|decision|idea|reminder",
"title":"under 60 chars","detail":"1-2 sentences","owner":"david|chris|either|unknown",
"urgency":"high|medium|low","privacy":"green|yellow|red",
"source_quote":"short phrase","status":"pending"}]}

Aim for 8-20 items. Return ONLY the JSON."""

NAMES_SYSTEM = """Generate 8-12 brand name candidates for a venture.

Return JSON only:
{"candidates":[{"name":"...","style":"literal|portmanteau|invented","reason":"..."}],
 "top_name":"...","top_domain":"... .com or .io"}"""

BRAND_SYSTEM = """Brand designer + copywriter building a landing page.

Return JSON only:
{"brand":{"primary_hex":"#RRGGBB","secondary_hex":"#RRGGBB","accent_hex":"#RRGGBB",
"tone":"friendly|bold|professional|playful"},
 "copy":{"headline":"...","subhead":"...","bullets":["...","...","..."],"cta_text":"..."}}"""

STORYLINE_SYSTEM = """Episode narrative for the ActiveFounder podcast (Outside Inc. audience).

HARD RULES:
1. NEVER name real people other than David or Chris.
2. NEVER discuss equity, deal terms, financial arrangements.
3. NEVER describe AI pipeline internals.

Lead with the outdoors. 200-350 words, 2-3 paragraphs, markdown."""

BUSINESS_PLAN_SYSTEM = """STAGE-APPROPRIATE business plan for an early-stage hike brainstorm.
Useful on the descent — concrete, short, honest.

Markdown ~1500-3000 words:
## TL;DR (2-3 sentences)
## The Idea
## Wedge / Differentiation
## Customer
## Revenue Model
## Build Plan (90 days)
## Open Questions (3-5)
## Kill Criteria (2 specific signals)
## Next Action (single)

Never name real people other than David and Chris. No AI-pipeline jargon."""

PERSONA_SYSTEM = """4-persona evaluation panel: CFO, CTO, CMO, COO.

Markdown:
## CFO Lens — Verdict (GO|CONDITIONAL|NO), Reasoning, 2-3 open questions
## CTO Lens — same shape
## CMO Lens — same shape
## COO Lens — same shape
## Synthesis — convergence + biggest unresolved question, <200 words"""

# AF prompt set: 7 prompts producing 7 .md outputs.
AF_PROMPT_SET = {
    "evaluation": f"{EVAL_SYSTEM}\n\nReturn the JSON only, no preamble.",
    "actions": f"{ACTIONS_SYSTEM}\n\nReturn the JSON only.",
    "names": f"{NAMES_SYSTEM}\n\nReturn the JSON only.",
    "brand": f"{BRAND_SYSTEM}\n\nReturn the JSON only.",
    "storyline": f"{STORYLINE_SYSTEM}\n\nWrite the narrative now.",
    "business_plan": f"{BUSINESS_PLAN_SYSTEM}\n\nWrite the plan now.",
    "persona_evaluation": f"{PERSONA_SYSTEM}\n\nWrite the evaluation now.",
}

AF_CONFIG = {
    "container_term": "episode",
    "tier_names": {"A": "ogg", "B": "iphone-direct", "C": "telegram-mp3"},
    "synthesis_prompts": AF_PROMPT_SET,
}

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_FILENAME_TIMESTAMP_RE = re.compile(r"_(\d{8})_(\d{6})_")


def _parse_filename_timestamp(filename: str) -> datetime:
    """Parse YYYYMMDD_HHMMSS embedded in DJI filenames into a UTC datetime.

    Filenames look like: TX01_MIC020_20260426_053310_orig.ogg
    Treat the time as UTC (we don't have TZ metadata; relative ordering
    is what matters for synthesis).
    """
    m = _FILENAME_TIMESTAMP_RE.search(filename)
    if not m:
        raise ValueError(f"No timestamp in filename: {filename}")
    date_str, time_str = m.groups()
    return datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def _list_existing_transcripts(s3, prefix: str) -> list[dict[str, str]]:
    """Return [{filename, json_key}, ...] for .ogg.json transcripts under prefix.

    synthesize._build_unified_transcript derives the .txt fallback key itself,
    so we only need to track the canonical .json path.
    """
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    out = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".ogg.json"):
            out.append({"filename": key.rsplit("/", 1)[-1], "json_key": key})
    out.sort(key=lambda d: d["filename"])
    return out


def _build_test1_manifest(transcripts: list[dict[str, str]]) -> Manifest:
    """Build a Manifest pointing at existing S3 transcripts.

    SHA256 is derived from the filename (deterministic, unique enough for the
    manifest's dedup logic). It is NOT the audio file SHA — but synthesize()
    only reads transcript_path, so this is sufficient for regression.
    """
    m = Manifest(episode=TEST1_CONTAINER)
    for t in transcripts:
        sha = hashlib.sha256(t["filename"].encode()).hexdigest()
        wall = _parse_filename_timestamp(t["filename"])
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=wall,
            duration_sec=1800.0,  # ~30 min DJI segment; not load-bearing for synthesis
            file_path=t["filename"],  # synthetic — never actually read
            transcript_path=t["json_key"],  # the load-bearing field
            participant=None,
        )
        m.add_audio(entry, episode=TEST1_CONTAINER)
    return m


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _fetch_baseline(s3, output_name: str) -> tuple[str, str]:
    """Return (text, ext_used) for the baseline output. Tries .md then .json."""
    for ext in (".md", ".json"):
        key = f"outputs/{BASELINE_CONTAINER}/{output_name}{ext}"
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            return obj["Body"].read().decode("utf-8"), ext
        except Exception:
            continue
    return "", ""


def _fetch_test_output(s3, container: str, output_name: str) -> str:
    """Fetch the test output (always .md from synthesize())."""
    key = f"outputs/{container}/{output_name}.md"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception:
        return ""


def _key_term_overlap(baseline: str, test: str) -> float:
    base_words = {w.lower().strip(".,!?;:\"'()[]{}") for w in baseline.split() if len(w) > 5}
    test_words = {w.lower().strip(".,!?;:\"'()[]{}") for w in test.split() if len(w) > 5}
    if not base_words:
        return 0.0
    overlap = len(base_words & test_words)
    return round(100.0 * overlap / len(base_words), 1)


def _compare(baseline: str, test: str, name: str, baseline_ext: str) -> dict:
    b_wc = _word_count(baseline)
    t_wc = _word_count(test)
    delta = round(100.0 * (t_wc - b_wc) / b_wc, 1) if b_wc else 0.0
    return {
        "output": name,
        "baseline_ext": baseline_ext or "missing",
        "baseline_wc": b_wc,
        "test_wc": t_wc,
        "delta_pct": delta,
        "key_term_overlap_pct": _key_term_overlap(baseline, test),
        "test_has_content": len(test.strip()) > 50,
    }


# ----------------------------------------------------------------------
# Test 1: synthesis-only regression
# ----------------------------------------------------------------------


def run_test1(s3) -> dict:
    log.info("=" * 60)
    log.info("TEST 1: josh-cashman synthesis-only regression")
    log.info("Container: %s", TEST1_CONTAINER)
    log.info("=" * 60)
    t0 = time.time()

    transcripts = _list_existing_transcripts(s3, TEST1_TRANSCRIPT_PREFIX)
    if len(transcripts) != 7:
        raise ValueError(f"Expected 7 josh-cashman transcripts, found {len(transcripts)}")
    log.info("Found %d transcripts", len(transcripts))
    for t in transcripts:
        log.info("  %s", t["filename"])

    manifest = _build_test1_manifest(transcripts)
    log.info("Built manifest with %d audio entries", len(manifest.audio_entries))

    log.info("Calling synthesize() with %d prompts (Sonnet 4.5)...", len(AF_PROMPT_SET))
    output_keys = synthesize(manifest, AF_PROMPT_SET, s3, BUCKET, partial=False)

    elapsed = time.time() - t0
    log.info("Test 1 synthesis complete in %.1fs", elapsed)

    log.info("Comparing outputs against baseline (%s):", BASELINE_CONTAINER)
    comparisons = []
    for name in AF_PROMPT_SET:
        baseline, ext = _fetch_baseline(s3, name)
        test_out = _fetch_test_output(s3, TEST1_CONTAINER, name)
        cmp_ = _compare(baseline, test_out, name, ext)
        comparisons.append(cmp_)
        log.info(
            "  %-22s baseline=%5d wc (%s)  test=%5d wc  delta=%+.1f%%  overlap=%.0f%%",
            name,
            cmp_["baseline_wc"],
            cmp_["baseline_ext"],
            cmp_["test_wc"],
            cmp_["delta_pct"],
            cmp_["key_term_overlap_pct"],
        )

    return {
        "test": "test1_josh_cashman_regression",
        "container": TEST1_CONTAINER,
        "elapsed_sec": round(elapsed, 1),
        "outputs_written": list(output_keys.keys()),
        "comparisons": comparisons,
    }


# ----------------------------------------------------------------------
# Test 2: fresh end-to-end
# ----------------------------------------------------------------------


def _compress_wav(src: Path, dst_dir: Path) -> Path:
    out = dst_dir / (src.stem + ".opus")
    if out.exists() and out.stat().st_size > 0:
        log.info("  cached: %s (%.1f MB)", out.name, out.stat().st_size / 1e6)
        return out
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        str(out),
    ]
    log.info("  compressing: %s -> %s", src.name, out.name)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {src.name}: {res.stderr[-300:]}")
    log.info("  done: %s (%.1f MB)", out.name, out.stat().st_size / 1e6)
    return out


def run_test2(s3) -> dict:
    log.info("=" * 60)
    log.info("TEST 2: ep-2026-04-19 fresh end-to-end")
    log.info("Container: %s", TEST2_CONTAINER)
    log.info("=" * 60)
    t0 = time.time()

    wav_paths = []
    for name in TEST2_WAVS:
        p = TEST2_WAV_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"WAV missing: {p}")
        wav_paths.append(p)
    total_gb = sum(p.stat().st_size for p in wav_paths) / 1e9
    log.info("Found %d WAVs (%.2f GB total)", len(wav_paths), total_gb)

    staging = Path(tempfile.mkdtemp(prefix="ep419_"))
    log.info("Staging dir: %s", staging)
    log.info("Compressing %d WAVs to opus 64kbps mono 16kHz...", len(wav_paths))
    opus_paths = [_compress_wav(p, staging) for p in wav_paths]

    pipeline = BatchPipeline(
        container=TEST2_CONTAINER,
        config=AF_CONFIG,
        s3_client=s3,
        bucket=BUCKET,
    )
    log.info("Running BatchPipeline.ingest() — uploads + Groq transcription...")
    pipeline.ingest(opus_paths)
    log.info(
        "Manifest now has %d entries. Running finalize()...", len(pipeline.manifest.audio_entries)
    )
    output_keys = pipeline.finalize()
    elapsed = time.time() - t0
    log.info("Test 2 complete in %.1fs", elapsed)

    storyline = _fetch_test_output(s3, TEST2_CONTAINER, "storyline")
    preview = (storyline[:600] + "...") if storyline else "(no storyline produced)"

    return {
        "test": "test2_ep_419_fresh_e2e",
        "container": TEST2_CONTAINER,
        "elapsed_sec": round(elapsed, 1),
        "outputs_written": list(output_keys.keys()),
        "manifest_entries": len(pipeline.manifest.audio_entries),
        "storyline_preview": preview,
    }


# ----------------------------------------------------------------------
# Acceptance gate
# ----------------------------------------------------------------------


def _structural_check(s3, container: str, name: str) -> tuple[bool, str]:
    """Validate that a synthesized output is well-formed and on-topic.

    LLM regen produces high vocabulary variance; word-overlap thresholds
    flap on stochastic rephrasing. Structural checks are stable.
    """
    key = f"outputs/{container}/{name}.md"
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")
    except Exception as exc:
        return False, f"missing from S3: {exc}"

    if name in ("evaluation", "actions", "names", "brand"):
        text = body.strip()
        if text.startswith("```json"):
            text = text[7:].rsplit("```", 1)[0]
        elif text.startswith("```"):
            text = text[3:].rsplit("```", 1)[0]
        try:
            data = json.loads(text)
        except Exception as exc:
            return False, f"JSON parse failed: {exc}"
        n_keys = len(data) if isinstance(data, dict) else len(data)
        return True, f"JSON parsed, {n_keys} keys/items"

    if name == "storyline":
        n_paras = body.count("\n\n")
        outdoors = any(
            w in body.lower() for w in ("hike", "trail", "peak", "outdoor", "climb", "mountain")
        )
        ok = n_paras >= 2 and outdoors
        return ok, f"{n_paras} paragraphs, outdoors_anchor={outdoors}"

    if name == "business_plan":
        sections = (
            "TL;DR",
            "Idea",
            "Wedge",
            "Customer",
            "Revenue",
            "Build Plan",
            "Open Questions",
            "Kill Criteria",
            "Next Action",
        )
        present = sum(1 for s in sections if s.lower() in body.lower())
        return present >= 7, f"{present}/{len(sections)} required sections"

    if name == "persona_evaluation":
        lenses = ("CFO", "CTO", "CMO", "COO", "Synthesis")
        present = sum(1 for s in lenses if s in body)
        return present >= 4, f"{present}/{len(lenses)} persona lenses"

    return True, "no structural rule defined"


def evaluate_test1(result: dict, s3) -> tuple[bool, list[str]]:
    """Acceptance criteria for Test 1.

    PASS conditions (all must hold):
    - All 7 outputs produced (test_has_content=True)
    - Word count within ±70% of baseline (broad LLM regen tolerance)
    - Each output passes its structural check (JSON parses, sections present, etc.)
    """
    failures = []
    for cmp_ in result["comparisons"]:
        name = cmp_["output"]
        if not cmp_["test_has_content"]:
            failures.append(f"{name}: empty test output")
            continue
        if cmp_["baseline_wc"] == 0:
            failures.append(f"{name}: baseline missing — cannot regress against nothing")
            continue
        if abs(cmp_["delta_pct"]) > 70.0:
            failures.append(f"{name}: word-count delta {cmp_['delta_pct']:+.1f}% exceeds ±70%")
        ok, detail = _structural_check(s3, result["container"], name)
        if not ok:
            failures.append(f"{name}: structural check FAILED — {detail}")
    return len(failures) == 0, failures


def evaluate_test2(result: dict) -> tuple[bool, list[str]]:
    """Acceptance criteria for Test 2.

    PASS conditions:
    - 7 outputs written (the AF prompt set)
    - Manifest has 7 audio entries
    - Storyline preview has content
    """
    failures = []
    expected = set(AF_PROMPT_SET.keys())
    written = set(result["outputs_written"])
    if expected - written:
        failures.append(f"missing outputs: {sorted(expected - written)}")
    if result["manifest_entries"] != 7:
        failures.append(f"manifest has {result['manifest_entries']} entries, expected 7")
    if "(no storyline" in result["storyline_preview"]:
        failures.append("no storyline produced")
    return len(failures) == 0, failures


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-test2", action="store_true", help="Run only the cheap Test 1 regression"
    )
    parser.add_argument("--only-test2", action="store_true", help="Run only Test 2 (skip Test 1)")
    args = parser.parse_args()

    for var in ("ANTHROPIC_API_KEY",):
        if not os.environ.get(var):
            log.error("MISSING env var: %s", var)
            return 1
    if not args.skip_test2 and not os.environ.get("GROQ_API_KEY"):
        log.error("Test 2 requires GROQ_API_KEY (use --skip-test2 to skip)")
        return 1
    log.info("ANTHROPIC_API_KEY: %d chars", len(os.environ["ANTHROPIC_API_KEY"]))
    if os.environ.get("GROQ_API_KEY"):
        log.info("GROQ_API_KEY: %d chars", len(os.environ["GROQ_API_KEY"]))

    s3 = boto3.client("s3", region_name=REGION)

    results: dict[str, object] = {}
    errors: dict[str, str] = {}
    gates: dict[str, dict] = {}

    if not args.only_test2:
        try:
            r1 = run_test1(s3)
            results["test1"] = r1
            ok, failures = evaluate_test1(r1, s3)
            gates["test1"] = {"pass": ok, "failures": failures}
            log.info("Test 1 acceptance: %s", "PASS" if ok else f"FAIL ({len(failures)})")
            for f in failures:
                log.warning("  - %s", f)
        except Exception as exc:
            log.exception("Test 1 raised")
            errors["test1"] = str(exc)
            gates["test1"] = {"pass": False, "failures": [f"exception: {exc}"]}

    if not args.skip_test2:
        try:
            r2 = run_test2(s3)
            results["test2"] = r2
            ok, failures = evaluate_test2(r2)
            gates["test2"] = {"pass": ok, "failures": failures}
            log.info("Test 2 acceptance: %s", "PASS" if ok else f"FAIL ({len(failures)})")
            for f in failures:
                log.warning("  - %s", f)
        except Exception as exc:
            log.exception("Test 2 raised")
            errors["test2"] = str(exc)
            gates["test2"] = {"pass": False, "failures": [f"exception: {exc}"]}

    log.info("=" * 60)
    log.info("RESULTS SUMMARY")
    log.info("=" * 60)
    summary = {"results": results, "errors": errors, "gates": gates}
    print(json.dumps(summary, indent=2, default=str))

    overall = all(g["pass"] for g in gates.values()) and not errors
    log.info("OVERALL: %s", "ACCEPTANCE PASSED" if overall else "ACCEPTANCE FAILED")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
