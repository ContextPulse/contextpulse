"""Phase B.1 validation — test the calibrated RMS margin against David's labeled
listen-test clips. For each clip whose speaker is identified in David's labels
AND that falls inside a parallel-mic window, compute per-mic RMS average over
the clip's wall-time, classify with the ±N dB rule, and compare to ground truth.

Speaker labels are encoded inline below (transcribed from David's chat replies
during cc-20260507-1230). They are AT the clip's nominal start_sec; for clips
where David noted a transition mid-clip ("starts X then Y"), we mark TRANSITION
and exclude from accuracy scoring.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


HOP_SEC = 0.1
DJI_DUMP = Path(r"C:\Users\david\Desktop\dji mic3")


# Hand-transcribed from David's chat replies. Speaker = whose VOICE is audible.
# UNKNOWN = clip was wearer-labeled only, no clear speaker info.
# TRANSITION = David noted a speaker change mid-clip; excluded from accuracy.
CLIP_LABELS = {
    # Pre-hike parallel
    "01_TX02_MIC036_20260426_053400_orig.wav":          {"speaker": "chris", "note": "Chris (Josh in bg)"},
    "08_TX02_MIC036_20260426_053400_orig__pos25.wav":   {"speaker": "chris"},
    "09_TX02_MIC036_20260426_053400_orig__pos75.wav":   {"speaker": "chris"},
    "22_TX01_MIC020_20260426_053310_orig__loud.wav":    {"speaker": "UNKNOWN", "note": "wearer Josh, speaker not specified"},
    "23_TX01_MIC020_20260426_053310_orig__pos25.wav":   {"speaker": "chris", "note": "Chris talking, Josh interjects"},
    "24_TX01_MIC020_20260426_053310_orig__pos75.wav":   {"speaker": "UNKNOWN", "note": "wearer Josh"},
    # Early-hike parallel
    # Clip 02 = MIC021 (Josh's mic), David label "Josh" -> speaker = Josh on his own mic
    # Clip 03 = MIC037 (Chris's mic), David label "Chris" -> speaker = Chris on his own mic
    "02_TX00_MIC021_20260426_060311_orig.wav":          {"speaker": "josh"},
    "03_TX00_MIC037_20260426_060400_orig.wav":          {"speaker": "chris"},
    "10_TX00_MIC021_20260426_060311_orig__pos25.wav":   {"speaker": "chris", "note": "only real audio is Chris"},
    "11_TX00_MIC021_20260426_060311_orig__pos75.wav":   {"speaker": "TRANSITION", "note": "starts Chris, then Josh"},
    "12_TX00_MIC037_20260426_060400_orig__pos25.wav":   {"speaker": "chris"},
    "13_TX00_MIC037_20260426_060400_orig__pos75.wav":   {"speaker": "josh", "note": "Chris doesn't speak in this clip"},
}


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_rms_for_window(audio_path: Path, start_sec: float, dur_sec: float) -> float:
    """Average RMS dBFS over [start_sec, start_sec + dur_sec] of audio_path."""
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        f.seek(int(start_sec * sr))
        n_samples = int(dur_sec * sr)
        block = f.read(n_samples, dtype="float32", always_2d=True)
    if block.shape[1] > 1:
        mono = block.mean(axis=1)
    else:
        mono = block[:, 0]
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    return 20.0 * np.log10(max(rms, 1e-6))


def find_chris_josh_at(mom: dict, t_utc: datetime, dur_sec: float) -> tuple[dict | None, dict | None]:
    """Return (chris_file_info, josh_file_info) covering t_utc through t_utc+dur."""
    t_end = t_utc + timedelta(seconds=dur_sec)
    chris, josh = None, None
    for wearer_id, info in mom["wearers"].items():
        for interval in info["intervals"]:
            f_start = parse_iso(interval["start_utc"])
            f_end = parse_iso(interval["end_utc"])
            if f_start <= t_utc and f_end >= t_end:
                rec = {
                    "filename": interval["source_file"],
                    "start_utc": f_start,
                    "path": DJI_DUMP / interval["source_file"],
                }
                if wearer_id == "chris":
                    chris = rec
                elif wearer_id == "josh":
                    josh = rec
    return chris, josh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--clip-sec", type=float, default=10.0)
    ap.add_argument("--margins", type=str, default="3,4,5,6,7.5,9")
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    mom = json.loads((ep_dir / "mic_owner_map.json").read_text(encoding="utf-8"))
    audit_dir = ep_dir / "mic_audit"

    # Build a lookup from clip filename -> (source_file, start_sec, wall_time_utc)
    # We re-derive these from the clip filenames + raw_sources rather than parsing manifests.
    raw = json.loads((ep_dir / "raw_sources.json").read_text(encoding="utf-8"))
    src_by_name = {Path(s["file_path"]).name: s for s in raw["sources"] if s.get("bwf_origination")}

    # Re-derive clip start_sec from filename suffix using same logic as the extractors.
    # For 01-07: loudest-window position - we'll recompute by reading the manifest if we can.
    # For 08-21 (__pos25 / __pos75): 25% / 75% of duration.
    # For 22-27: depends on which extractor — we'll just look up the BWF + offset.
    # Simpler: read MANIFEST.md tables to pull start_sec.

    def parse_manifest_md(md_path: Path) -> dict[str, dict]:
        out = {}
        if not md_path.exists():
            return out
        for line in md_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|") or "Clip" in line or "---" in line:
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 4:
                continue
            clip = parts[0].strip("`")
            src = parts[1].strip("`")
            try:
                # Different manifests place start_sec at different columns. Find the integer column.
                start_sec = None
                for p in parts:
                    p_clean = p.strip()
                    try:
                        v = float(p_clean)
                        if 0 <= v <= 2000:
                            start_sec = v
                            break
                    except ValueError:
                        continue
                if start_sec is None:
                    continue
                out[clip] = {"source_file": src, "start_sec": start_sec}
            except Exception:
                continue
        return out

    clip_meta = {}
    for fname in ["MANIFEST.md", "MANIFEST_extra.md", "MANIFEST_uningested.md"]:
        clip_meta.update(parse_manifest_md(audit_dir / fname))

    print(f"Loaded clip metadata for {len(clip_meta)} clips")

    # Build the validation table
    margins = [float(m) for m in args.margins.split(",")]
    print(f"Validation margins to test: {margins}")
    print()

    # For each labeled clip, compute Chris/Josh RMS at the clip's wall-time
    rows = []
    for clip_name, label_info in CLIP_LABELS.items():
        meta = clip_meta.get(clip_name)
        if meta is None:
            print(f"  no manifest entry for {clip_name}, skipping")
            continue
        src_file = meta["source_file"]
        start_sec = meta["start_sec"]
        src_record = src_by_name.get(src_file)
        if src_record is None:
            print(f"  no raw_sources entry for {src_file}, skipping")
            continue
        bwf = parse_iso(src_record["bwf_origination"])
        clip_start_utc = bwf + timedelta(seconds=start_sec)

        chris, josh = find_chris_josh_at(mom, clip_start_utc, args.clip_sec)
        if chris is None or josh is None:
            rows.append({
                "clip": clip_name, "speaker": label_info["speaker"],
                "wall_time": clip_start_utc.isoformat(),
                "in_parallel": False, "chris_dB": None, "josh_dB": None, "delta_dB": None,
                "skip_reason": "outside parallel window (missing chris or josh file)",
            })
            continue

        chris_offset = (clip_start_utc - chris["start_utc"]).total_seconds()
        josh_offset  = (clip_start_utc - josh["start_utc"]).total_seconds()
        chris_dB = compute_rms_for_window(chris["path"], chris_offset, args.clip_sec)
        josh_dB  = compute_rms_for_window(josh["path"],  josh_offset,  args.clip_sec)

        rows.append({
            "clip": clip_name,
            "speaker": label_info["speaker"],
            "wall_time": clip_start_utc.isoformat(),
            "in_parallel": True,
            "chris_dB": chris_dB,
            "josh_dB": josh_dB,
            "delta_dB": chris_dB - josh_dB,
            "note": label_info.get("note", ""),
        })

    # Print table
    print(f"{'#':3s} {'Clip':50s} {'Speaker':10s} {'Wall':18s} {'Chris dB':>9s} {'Josh dB':>9s} {'Delta':>7s}  Note")
    print("-" * 140)
    for r in rows:
        if not r["in_parallel"]:
            print(f"    {r['clip']:50s} {r['speaker']:10s} {r['wall_time'][:19]:18s} {'--':>9s} {'--':>9s} {'--':>7s}  ({r['skip_reason']})")
        else:
            print(f"    {r['clip']:50s} {r['speaker']:10s} {r['wall_time'][:19]:18s} {r['chris_dB']:>+9.1f} {r['josh_dB']:>+9.1f} {r['delta_dB']:>+7.1f}  {r.get('note', '')}")

    # Accuracy at each margin
    print()
    scoreable = [r for r in rows if r["in_parallel"] and r["speaker"] in ("chris", "josh")]
    print(f"Scoreable rows: {len(scoreable)}")

    for margin in margins:
        correct = wrong = ambig = 0
        details = []
        for r in scoreable:
            d = r["delta_dB"]
            if d > margin:
                pred = "chris"
            elif d < -margin:
                pred = "josh"
            else:
                pred = "ambig"
            if pred == r["speaker"]:
                correct += 1
                details.append("OK ")
            elif pred == "ambig":
                ambig += 1
                details.append("? ")
            else:
                wrong += 1
                details.append("X ")
        n = len(scoreable)
        print(f"  margin ±{margin:4.1f} dB:  correct={correct}/{n}  wrong={wrong}/{n}  ambiguous={ambig}/{n}  {''.join(details)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
