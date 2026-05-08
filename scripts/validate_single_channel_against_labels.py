"""Phase B.2/B.3 validation — test the single-channel thresholds on labeled
clips that fall in the Josh-only window (12:19:20 - 14:59:08 UTC).

For each clip, compute Josh-mic average RMS over its 10-sec window, classify
with the calibrated thresholds, and compare to David's speaker label.

Speaker labels are encoded inline (transcribed from David's chat replies during
cc-20260507-1230). PRIMARY = the speaker David identified as the main audible
voice during the clip. MIXED = multiple speakers active during the clip.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


DJI_DUMP = Path(r"C:\Users\david\Desktop\dji mic3")


# Speaker labels for clips falling in the Josh-only single-mic window.
# Format: PRIMARY = main audible speaker; SECONDARY = anyone else mentioned.
CLIP_LABELS_SINGLE_MIC = {
    "04_TX00_MIC022_20260426_063311_orig.wav":          {"primary": "josh",  "secondary": None},
    "14_TX00_MIC022_20260426_063311_orig__pos25.wav":   {"primary": "david", "secondary": "josh", "note": "David talking, Josh just says uh huh and wow"},
    "15_TX00_MIC022_20260426_063311_orig__pos75.wav":   {"primary": "josh",  "secondary": None},
    "05_TX00_MIC023_20260426_070311_orig.wav":          {"primary": "josh",  "secondary": None},
    "16_TX00_MIC023_20260426_070311_orig__pos25.wav":   {"primary": "josh",  "secondary": None},
    "17_TX00_MIC023_20260426_070311_orig__pos75.wav":   {"primary": "josh",  "secondary": None},
    "25_TX01_MIC024_20260426_073311_orig__loud.wav":    {"primary": "josh",  "secondary": None},
    "26_TX01_MIC024_20260426_073311_orig__pos25.wav":   {"primary": "josh",  "secondary": None},
    "27_TX01_MIC024_20260426_073311_orig__pos75.wav":   {"primary": "josh",  "secondary": None},
    "06_TX00_MIC025_20260426_080311_orig.wav":          {"primary": "josh",  "secondary": None},
    "18_TX00_MIC025_20260426_080311_orig__pos25.wav":   {"primary": "chris", "secondary": "josh", "note": "Josh's mic but Chris only real audio in background"},
    "19_TX00_MIC025_20260426_080311_orig__pos75.wav":   {"primary": "MIXED", "secondary": "josh+chris", "note": "Josh talking at start, Chris responding in background"},
    "07_TX00_MIC026_20260426_083311_orig.wav":          {"primary": "josh",  "secondary": None},
    "20_TX00_MIC026_20260426_083311_orig__pos25.wav":   {"primary": "chris", "secondary": "josh", "note": "Josh mic, Chris talking in background"},
    "21_TX00_MIC026_20260426_083311_orig__pos75.wav":   {"primary": "chris", "secondary": "josh", "note": "Josh mic, Chris only real audio in background"},
    "11_TX00_MIC021_20260426_060311_orig__pos75.wav":   {"primary": "MIXED", "secondary": "chris+josh", "note": "starts Chris in bg, then Josh speaks up"},
}


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_rms_for_window(audio_path: Path, start_sec: float, dur_sec: float) -> float:
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        f.seek(int(start_sec * sr))
        block = f.read(int(dur_sec * sr), dtype="float32", always_2d=True)
    mono = block.mean(axis=1) if block.shape[1] > 1 else block[:, 0]
    rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
    return 20.0 * np.log10(max(rms, 1e-6))


def parse_manifest_md(md_path: Path) -> dict:
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
        start_sec = None
        for p in parts:
            try:
                v = float(p.strip())
                if 0 <= v <= 2000:
                    start_sec = v
                    break
            except ValueError:
                continue
        if start_sec is not None:
            out[clip] = {"source_file": src, "start_sec": start_sec}
    return out


def main() -> int:
    ep_dir = Path("working/ep-2026-04-26-josh-cashman")
    audit_dir = ep_dir / "mic_audit"
    mom = json.loads((ep_dir / "mic_owner_map.json").read_text(encoding="utf-8"))
    cal = json.loads((ep_dir / "single_channel_calibration.json").read_text(encoding="utf-8"))
    raw = json.loads((ep_dir / "raw_sources.json").read_text(encoding="utf-8"))

    src_by_name = {Path(s["file_path"]).name: s for s in raw["sources"] if s.get("bwf_origination")}

    clip_meta = {}
    for fname in ["MANIFEST.md", "MANIFEST_extra.md", "MANIFEST_uningested.md"]:
        clip_meta.update(parse_manifest_md(audit_dir / fname))

    # Map filename -> wearer
    file_to_wearer = {}
    for wearer, info in mom["wearers"].items():
        for interval in info["intervals"]:
            file_to_wearer[interval["source_file"]] = wearer

    # Threshold definitions
    T_high = cal["single_channel_thresholds_josh_mic"]["T_high_above_is_josh_direct"]
    T_silence = cal["single_channel_thresholds_josh_mic"]["T_silence_below_is_silence"]
    T_bleed_top = cal["single_channel_thresholds_josh_mic"]["T_bleed_top_below_is_bleed"]

    print(f"Single-channel thresholds (Josh-mic):")
    print(f"  > {T_high:+.1f} dB        -> Josh-direct")
    print(f"  {T_silence:+.1f} to {T_high:+.1f} dB  -> ambiguous (David / quiet-Josh / Chris-loud-bleed)")
    print(f"  < {T_silence:+.1f} dB        -> silence or Chris-quiet-bleed")
    print()

    rows = []
    for clip_name, label in CLIP_LABELS_SINGLE_MIC.items():
        meta = clip_meta.get(clip_name)
        if meta is None:
            print(f"  no manifest entry for {clip_name}, skipping")
            continue
        src_file = meta["source_file"]
        start_sec = meta["start_sec"]
        wearer = file_to_wearer.get(src_file)
        if wearer != "josh":
            print(f"  {clip_name} not from Josh's mic (wearer={wearer}), skipping")
            continue
        src_record = src_by_name.get(src_file)
        if src_record is None:
            continue
        bwf = parse_iso(src_record["bwf_origination"])
        clip_start_utc = bwf + timedelta(seconds=start_sec)

        # Skip clips that fall in parallel window — those are already validated separately
        if clip_start_utc < parse_iso("2026-04-26T12:19:20Z"):
            continue

        josh_path = DJI_DUMP / src_file
        josh_dB = compute_rms_for_window(josh_path, start_sec, 10.0)

        # Classify
        if josh_dB > T_high:
            pred = "josh"
        elif josh_dB < T_silence:
            pred = "silence-or-chris-quiet"
        else:
            pred = "ambig (david / quiet-josh / chris-loud-bleed)"

        rows.append({
            "clip": clip_name,
            "wall": clip_start_utc.isoformat()[:19],
            "primary": label["primary"],
            "secondary": label.get("secondary"),
            "josh_dB": josh_dB,
            "pred": pred,
            "note": label.get("note", ""),
        })

    # Print
    print(f"{'Clip':50s} {'Wall':18s} {'Primary':8s} {'Josh dB':>8s}  {'Predicted':40s}  Note")
    print("-" * 160)
    for r in rows:
        print(f"{r['clip']:50s} {r['wall']:18s} {r['primary']:8s} {r['josh_dB']:>+8.1f}  {r['pred']:40s}  {r['note']}")

    # Score
    print()
    correct_josh = sum(1 for r in rows if r["primary"] == "josh" and r["pred"] == "josh")
    josh_total = sum(1 for r in rows if r["primary"] == "josh")
    chris_in_bleed = sum(1 for r in rows if r["primary"] == "chris" and "ambig" in r["pred"] or "silence" in r["pred"])
    chris_total = sum(1 for r in rows if r["primary"] == "chris")
    david_in_ambig = sum(1 for r in rows if r["primary"] == "david" and "ambig" in r["pred"])
    david_total = sum(1 for r in rows if r["primary"] == "david")

    print(f"Performance:")
    print(f"  Josh-primary clips correctly classified as Josh:   {correct_josh}/{josh_total}")
    print(f"  Chris-primary clips correctly NOT classified Josh: {chris_in_bleed}/{chris_total}  (should be ambig or silence-or-chris)")
    print(f"  David-primary clips correctly classified as ambig: {david_in_ambig}/{david_total}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
