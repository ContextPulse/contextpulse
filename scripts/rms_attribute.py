"""Phase C — RMS-based speaker attribution. Combined script that handles both
strategies based on mic_owner_map.json windows:

- parallel_rms strategy (when both mics are recording): cross-channel dB
  dominance with the calibrated +/-6 dB margin (rms_calibration.json).

- single_channel_threshold strategy (when only Josh's mic is recording):
  amplitude-based classification with thresholds from
  single_channel_calibration.json.

- single_wearer_direct strategy (when only one wearer is recording and no
  other speakers are present): direct attribution to the wearer.

For each segment, we compute the average RMS over its wall-time interval on
each mic that's recording during that interval, then apply the rule for the
window the segment falls in. Output is unified_attributed_rms.json with
per-segment attribution + confidence + signals.

Segments where amplitude alone can't decide (the 'ambiguous' bucket in single-
channel mode, or |delta|<margin in parallel mode) are flagged 'ambig' and
left for Phase D (content fingerprints) to disambiguate.
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


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_rms_curve(audio_path: Path) -> np.ndarray:
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        audio = f.read(dtype="float32", always_2d=True)
    mono = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
    hop_samples = int(round(HOP_SEC * sr))
    n = len(mono) // hop_samples
    blk = mono[: n * hop_samples].reshape(n, hop_samples).astype(np.float64)
    rms = np.sqrt(np.mean(blk ** 2, axis=1))
    with np.errstate(divide="ignore"):
        return (20.0 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)


def rms_over_interval(rms_curve: np.ndarray, file_start: datetime,
                      seg_start: datetime, seg_end: datetime) -> float | None:
    """Average RMS-dBFS over [seg_start, seg_end] using rms_curve aligned to file_start."""
    s = (seg_start - file_start).total_seconds()
    e = (seg_end - file_start).total_seconds()
    i0 = max(0, int(round(s / HOP_SEC)))
    i1 = min(len(rms_curve), int(round(e / HOP_SEC)))
    if i1 <= i0:
        return None
    # Average in linear amplitude domain (energy mean) then convert back to dB
    lin = np.power(10.0, rms_curve[i0:i1] / 20.0).astype(np.float64)
    avg = float(np.sqrt(np.mean(lin ** 2)))
    return 20.0 * np.log10(max(avg, 1e-6))


def find_window_for_time(windows: list, t: datetime) -> dict | None:
    for w in windows:
        if parse_iso(w["start_utc"]) <= t < parse_iso(w["end_utc"]):
            return w
    return None


def find_file_at_time(intervals: list, t: datetime) -> dict | None:
    for iv in intervals:
        if parse_iso(iv["start_utc"]) <= t < parse_iso(iv["end_utc"]):
            return iv
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    ap.add_argument("--margin-db", type=float, default=6.0)
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    transcript = json.loads((ep_dir / "fingerprint" / "unified_transcript_labeled_dji_only.json").read_text(encoding="utf-8"))
    mom = json.loads((ep_dir / "mic_owner_map.json").read_text(encoding="utf-8"))
    sc_cal = json.loads((ep_dir / "single_channel_calibration.json").read_text(encoding="utf-8"))

    T_high = sc_cal["single_channel_thresholds_josh_mic"]["T_high_above_is_josh_direct"]
    T_silence = sc_cal["single_channel_thresholds_josh_mic"]["T_silence_below_is_silence"]

    # Pre-compute RMS curves for files we'll need
    rms_cache: dict[str, tuple[np.ndarray, datetime]] = {}
    for wearer_id, info in mom["wearers"].items():
        for iv in info["intervals"]:
            fname = iv["source_file"]
            if fname in rms_cache:
                continue
            path = DJI_DUMP / fname
            if not path.exists():
                print(f"  MISSING audio: {path}")
                continue
            print(f"  Computing RMS curve for {fname}...", end=" ", flush=True)
            curve = compute_rms_curve(path)
            rms_cache[fname] = (curve, parse_iso(iv["start_utc"]))
            print(f"done ({len(curve)} hops)")

    # Build per-wearer interval lookup for O(N) per segment
    wearer_intervals = {wearer_id: info["intervals"] for wearer_id, info in mom["wearers"].items()}

    out_segments = []
    stats = {"chris": 0, "josh": 0, "david": 0, "ambig": 0, "silence": 0, "unattributed": 0,
             "by_strategy": {}}

    print(f"\nAttributing {len(transcript['segments'])} segments...")
    for seg in transcript["segments"]:
        seg_start = parse_iso(seg["wall_start_utc"])
        seg_end = parse_iso(seg["wall_end_utc"])
        seg_mid = seg_start + (seg_end - seg_start) / 2

        window = find_window_for_time(mom["windows"], seg_mid)
        out = dict(seg)  # copy original fields
        out["mic_owner_window"] = window["label"] if window else None

        if window is None:
            out["attributed_speaker"] = None
            out["confidence"] = 0.0
            out["signals"] = {"reason": "no window covers this segment"}
            stats["unattributed"] += 1
            out_segments.append(out)
            continue

        strategy = window["strategy"]
        stats["by_strategy"].setdefault(strategy, 0)
        stats["by_strategy"][strategy] += 1

        if strategy == "parallel_rms":
            chris_iv = find_file_at_time(wearer_intervals["chris"], seg_mid)
            josh_iv  = find_file_at_time(wearer_intervals["josh"],  seg_mid)
            chris_rms = josh_rms = None
            if chris_iv and chris_iv["source_file"] in rms_cache:
                curve, fstart = rms_cache[chris_iv["source_file"]]
                chris_rms = rms_over_interval(curve, fstart, seg_start, seg_end)
            if josh_iv and josh_iv["source_file"] in rms_cache:
                curve, fstart = rms_cache[josh_iv["source_file"]]
                josh_rms = rms_over_interval(curve, fstart, seg_start, seg_end)

            signals = {"chris_dB": chris_rms, "josh_dB": josh_rms}
            if chris_rms is None or josh_rms is None:
                speaker, conf = None, 0.0
                signals["reason"] = "missing one mic file"
                stats["unattributed"] += 1
            else:
                delta = chris_rms - josh_rms
                signals["delta_dB"] = delta
                if delta > args.margin_db:
                    speaker, conf = "chris", min(1.0, abs(delta) / 20.0)
                    stats["chris"] += 1
                elif delta < -args.margin_db:
                    speaker, conf = "josh", min(1.0, abs(delta) / 20.0)
                    stats["josh"] += 1
                else:
                    # Both voiced + similar levels = David (most likely)
                    if max(chris_rms, josh_rms) > -45.0:
                        speaker, conf = "david", 0.5  # provisional, Phase D may upgrade
                        stats["david"] += 1
                    else:
                        speaker, conf = "silence", 0.7
                        stats["silence"] += 1

            out["attributed_speaker"] = speaker
            out["confidence"] = conf
            out["signals"] = signals

        elif strategy == "single_channel_threshold":
            josh_iv = find_file_at_time(wearer_intervals["josh"], seg_mid)
            josh_rms = None
            if josh_iv and josh_iv["source_file"] in rms_cache:
                curve, fstart = rms_cache[josh_iv["source_file"]]
                josh_rms = rms_over_interval(curve, fstart, seg_start, seg_end)

            signals = {"josh_dB": josh_rms, "T_high": T_high, "T_silence": T_silence}
            if josh_rms is None:
                speaker, conf = None, 0.0
                signals["reason"] = "no josh mic file"
                stats["unattributed"] += 1
            elif josh_rms > T_high:
                speaker, conf = "josh", min(1.0, (josh_rms - T_high + 5.0) / 15.0)
                stats["josh"] += 1
            elif josh_rms < T_silence:
                speaker, conf = "silence", 0.8  # could be Chris very-quiet bleed; Phase D may correct
                stats["silence"] += 1
            else:
                # ambig zone: David / quiet-Josh / Chris-loud-bleed — Phase D disambiguates
                speaker, conf = "ambig", 0.4
                stats["ambig"] += 1

            out["attributed_speaker"] = speaker
            out["confidence"] = conf
            out["signals"] = signals

        elif strategy == "single_wearer_direct":
            wearers_recording = window.get("wearers_recording", [])
            if len(wearers_recording) == 1:
                speaker = wearers_recording[0]
                speaker_name = mom["wearers"][speaker]["display_name"].split()[0].lower()
                out["attributed_speaker"] = speaker_name
                out["confidence"] = 1.0
                out["signals"] = {"reason": "single wearer, no other speakers", "wearer": speaker}
                stats[speaker_name] = stats.get(speaker_name, 0) + 1
            else:
                out["attributed_speaker"] = None
                out["confidence"] = 0.0
                out["signals"] = {"reason": "single_wearer_direct but >1 wearer recording"}
                stats["unattributed"] += 1

        else:
            out["attributed_speaker"] = None
            out["confidence"] = 0.0
            out["signals"] = {"reason": f"unknown strategy {strategy}"}
            stats["unattributed"] += 1

        out_segments.append(out)

    # Write output
    out_data = {
        "session_id": mom["session_id"],
        "schema_version": "v1",
        "attribution_version": "rms-v1-hybrid",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_segments": len(out_segments),
        "stats": stats,
        "config": {
            "margin_dB": args.margin_db,
            "T_high_dBFS": T_high,
            "T_silence_dBFS": T_silence,
        },
        "segments": out_segments,
    }
    out_path = ep_dir / "unified_attributed_rms.json"
    out_path.write_text(json.dumps(out_data, indent=2, default=str), encoding="utf-8")

    print(f"\n=== Attribution stats ===")
    print(f"Total segments: {len(out_segments)}")
    print(f"By speaker:")
    for k in ["chris", "josh", "david", "ambig", "silence", "unattributed"]:
        n = stats.get(k, 0)
        print(f"  {k:12s}: {n:5d}  ({100*n/len(out_segments):.1f}%)")
    print(f"By strategy:")
    for k, n in stats["by_strategy"].items():
        print(f"  {k:30s}: {n:5d}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
