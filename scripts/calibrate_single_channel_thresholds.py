"""Phase B.2 + B.3 — derive single-channel attribution thresholds for the
Josh-only window (12:19:20 - 14:59:08 UTC) using the parallel-window data as
ground truth.

Strategy: in the parallel window, we know who's speaking via cross-channel
RMS dominance. So we can label each 100-ms hop as Josh-direct / Chris-bleed /
ambiguous (David / cross-talk) / silence, then measure what Josh's mic RMS
distribution looks like in each bucket. Those distributions become the
threshold bands for the single-channel window where only Josh's mic exists.

Inputs:
  working/<episode>/rms_curves_parallel.npz   — per-hop chris_dB, josh_dB
  working/<episode>/rms_calibration.json      — for VAD threshold + selected margin

Outputs:
  working/<episode>/single_channel_calibration.json  — thresholds + per-bucket stats
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def percentiles(arr: np.ndarray, ps: list[int]) -> dict:
    if len(arr) == 0:
        return {f"p{p}": None for p in ps}
    return {f"p{p}": float(np.percentile(arr, p)) for p in ps}


def text_histogram(arr: np.ndarray, lo: float, hi: float, step: float, max_width: int = 50) -> str:
    if len(arr) == 0:
        return "  (empty)"
    bins = np.arange(lo, hi + step, step)
    hist, edges = np.histogram(arr, bins=bins)
    if hist.max() == 0:
        return "  (no values in range)"
    lines = []
    for i in range(len(hist)):
        bar = "#" * int(hist[i] / hist.max() * max_width)
        lines.append(f"  {edges[i]:+6.1f} to {edges[i+1]:+6.1f}: {hist[i]:5d}  {bar}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-dir", default="working/ep-2026-04-26-josh-cashman")
    # "Strongly dominant" thresholds — used to confidently bucket each hop.
    # Wider than the +/-6 dB attribution margin to keep the buckets pure.
    ap.add_argument("--strong-dominant-db", type=float, default=12.0)
    # "David / ambiguous" zone — both mics voiced but neither dominates.
    ap.add_argument("--ambiguous-band-db", type=float, default=6.0)
    args = ap.parse_args()

    ep_dir = Path(args.episode_dir)
    npz = np.load(ep_dir / "rms_curves_parallel.npz")
    chris_dB = npz["chris_dB"].astype(np.float64)
    josh_dB = npz["josh_dB"].astype(np.float64)
    delta_dB = chris_dB - josh_dB
    n = len(chris_dB)

    cal = json.loads((ep_dir / "rms_calibration.json").read_text(encoding="utf-8"))
    vad = cal["vad_threshold_dBFS"]
    print(f"Total parallel hops: {n}  ({n * 0.1 / 60:.1f} min)")
    print(f"VAD threshold:       {vad:.1f} dBFS")
    print(f"Strong dominance:    +/-{args.strong_dominant_db:.0f} dB")
    print(f"Ambiguous band:      |delta| <= {args.ambiguous_band_db:.0f} dB and both voiced")
    print()

    # Bucket each hop
    chris_voiced = chris_dB > vad
    josh_voiced  = josh_dB  > vad
    any_voiced = chris_voiced | josh_voiced
    both_voiced = chris_voiced & josh_voiced

    josh_direct  = (delta_dB < -args.strong_dominant_db) & any_voiced
    chris_direct = (delta_dB > +args.strong_dominant_db) & any_voiced
    ambig_band   = (np.abs(delta_dB) <= args.ambiguous_band_db) & both_voiced
    silence      = ~any_voiced

    n_jd = int(josh_direct.sum())
    n_cd = int(chris_direct.sum())
    n_am = int(ambig_band.sum())
    n_sl = int(silence.sum())
    n_ot = n - n_jd - n_cd - n_am - n_sl
    print(f"Bucket counts (each hop = 100 ms):")
    print(f"  Josh-strongly-direct  (delta < -{args.strong_dominant_db:.0f}): {n_jd:6d}  ({100*n_jd/n:5.1f}%)")
    print(f"  Chris-strongly-direct (delta > +{args.strong_dominant_db:.0f}): {n_cd:6d}  ({100*n_cd/n:5.1f}%)")
    print(f"  Ambig band (both voiced, |delta|<= {args.ambiguous_band_db:.0f}): {n_am:6d}  ({100*n_am/n:5.1f}%)  <- David / cross-talk candidates")
    print(f"  Silence (both below VAD):                    {n_sl:6d}  ({100*n_sl/n:5.1f}%)")
    print(f"  Other (transition zones):                    {n_ot:6d}  ({100*n_ot/n:5.1f}%)")
    print()

    # === The key derivation: what does JOSH's mic look like in each bucket? ===
    # This gives us the single-channel thresholds.
    pcts = [5, 10, 25, 50, 75, 90, 95]
    print("=== Josh-mic dBFS distribution per bucket (this drives single-channel thresholds) ===")
    print(f"{'bucket':30s}  " + "  ".join(f"p{p:02d}" for p in pcts) + "    n")
    for label, mask in [
        ("Josh-direct (close-talk)",   josh_direct),
        ("Chris-direct (Chris-bleed)", chris_direct),
        ("Ambig (David / cross-talk)", ambig_band),
        ("Silence",                    silence),
    ]:
        josh_arr = josh_dB[mask]
        ps = np.percentile(josh_arr, pcts) if len(josh_arr) > 0 else [np.nan] * len(pcts)
        print(f"  {label:28s}  " + "  ".join(f"{v:+5.1f}" for v in ps) + f"  {len(josh_arr):6d}")
    print()

    # And same for Chris-mic (informs B.3 — what David sounds like on Chris's mic)
    print("=== Chris-mic dBFS distribution per bucket (corroborates the picture) ===")
    print(f"{'bucket':30s}  " + "  ".join(f"p{p:02d}" for p in pcts) + "    n")
    for label, mask in [
        ("Josh-direct (Josh-bleed)",  josh_direct),
        ("Chris-direct (close-talk)", chris_direct),
        ("Ambig (David / cross-talk)", ambig_band),
        ("Silence",                    silence),
    ]:
        chris_arr = chris_dB[mask]
        ps = np.percentile(chris_arr, pcts) if len(chris_arr) > 0 else [np.nan] * len(pcts)
        print(f"  {label:28s}  " + "  ".join(f"{v:+5.1f}" for v in ps) + f"  {len(chris_arr):6d}")
    print()

    # === Histogram visualisation: Josh-mic RMS overlap between buckets ===
    print("=== Josh-mic RMS histogram (compare overlap between buckets) ===")
    print()
    print("Josh-direct (Josh's voice on Josh's mic, close-talk):")
    print(text_histogram(josh_dB[josh_direct], -60, -5, 2.5))
    print()
    print("Chris-direct (Chris's voice as BLEED on Josh's mic):")
    print(text_histogram(josh_dB[chris_direct], -60, -5, 2.5))
    print()
    print("Ambig (David's voice / cross-talk on Josh's mic):")
    print(text_histogram(josh_dB[ambig_band], -60, -5, 2.5))
    print()
    print("Silence (Josh's mic noise floor):")
    print(text_histogram(josh_dB[silence], -80, -25, 2.5))
    print()

    # === Threshold derivation ===
    # T_high  (above this = confidently Josh-direct):  p25 of Josh-direct (Josh-mic)
    # T_bleed (above this = voice activity, but not confidently Josh-direct):
    #          p75 of Chris-bleed-on-Josh-mic — typical bleed level
    # T_silence (below this = silence): p95 of silence-bucket Josh-mic RMS + 3 dB
    josh_direct_p = np.percentile(josh_dB[josh_direct], pcts) if n_jd else None
    chris_bleed_p = np.percentile(josh_dB[chris_direct], pcts) if n_cd else None
    ambig_p       = np.percentile(josh_dB[ambig_band],   pcts) if n_am else None
    silence_p     = np.percentile(josh_dB[silence],      pcts) if n_sl else None

    # Default heuristic thresholds — tunable later
    T_high = float(np.percentile(josh_dB[josh_direct], 25)) if n_jd else None      # Josh-direct lower bound
    T_bleed_top = float(np.percentile(josh_dB[chris_direct], 75)) if n_cd else None  # bleed upper bound
    T_silence = float(np.percentile(josh_dB[silence], 95)) + 3.0 if n_sl else None
    # The "unsure / ambiguous" band is the OVERLAP region between bleed-top and direct-bottom.
    overlap_lo = min(T_bleed_top, T_high) if (T_high is not None and T_bleed_top is not None) else None
    overlap_hi = max(T_bleed_top, T_high) if (T_high is not None and T_bleed_top is not None) else None

    print("=== Recommended single-channel thresholds (Josh-mic only) ===")
    print(f"  T_high (Josh-direct above):  {T_high:+.1f} dBFS  (p25 of Josh-direct distribution)")
    print(f"  T_bleed_top (bleed below):   {T_bleed_top:+.1f} dBFS  (p75 of Chris-bleed-on-Josh-mic)")
    print(f"  T_silence (silence below):   {T_silence:+.1f} dBFS  (silence-p95 + 3 dB)")
    if overlap_lo is not None and overlap_hi is not None and overlap_hi > overlap_lo:
        print(f"  Overlap zone (uncertain):    {overlap_lo:+.1f} to {overlap_hi:+.1f} dBFS  (Josh-direct vs Chris-bleed indistinguishable on amplitude alone)")

    # Estimate accuracy of each threshold rule on the labeled buckets
    if T_high is not None:
        josh_high_correct = float((josh_dB[josh_direct] > T_high).mean())
        chris_falsepos    = float((josh_dB[chris_direct] > T_high).mean())
        david_falsepos    = float((josh_dB[ambig_band] > T_high).mean())
        print()
        print(f"=== Rule 'Josh-mic > {T_high:+.1f} dB => Josh' performance ===")
        print(f"  TP rate on Josh-direct bucket:  {100*josh_high_correct:.1f}% (caught Josh)")
        print(f"  FP rate from Chris-bleed bucket:{100*chris_falsepos:.1f}%   (Chris bleed misclassified as Josh)")
        print(f"  FP rate from ambig bucket:      {100*david_falsepos:.1f}%   (David / cross-talk misclassified as Josh)")
    if T_silence is not None:
        not_silence_above = float((josh_dB[silence] > T_silence).mean())
        josh_below_silence = float((josh_dB[josh_direct] < T_silence).mean()) if n_jd else 0.0
        print(f"=== Rule 'Josh-mic < {T_silence:+.1f} dB => silence' performance ===")
        print(f"  TN rate on silence bucket:      {100*(1-not_silence_above):.1f}%")
        print(f"  FN rate from Josh-direct:       {100*josh_below_silence:.1f}%   (Josh-direct misclassified as silence)")

    # Save
    out = {
        "session_id": cal["session_id"],
        "schema_version": "v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "calibration_source": "parallel_window_distributions",
        "strong_dominance_threshold_dB": args.strong_dominant_db,
        "ambiguous_band_dB": args.ambiguous_band_db,
        "vad_threshold_dBFS": vad,
        "bucket_counts": {
            "josh_direct": n_jd,
            "chris_direct": n_cd,
            "ambiguous_both_voiced": n_am,
            "silence": n_sl,
            "transition_other": n_ot,
            "total": n,
        },
        "josh_mic_dBFS_per_bucket_percentiles": {
            "josh_direct":   percentiles(josh_dB[josh_direct], pcts) if n_jd else None,
            "chris_bleed":   percentiles(josh_dB[chris_direct], pcts) if n_cd else None,
            "ambiguous":     percentiles(josh_dB[ambig_band],  pcts) if n_am else None,
            "silence":       percentiles(josh_dB[silence],     pcts) if n_sl else None,
        },
        "chris_mic_dBFS_per_bucket_percentiles": {
            "josh_bleed":    percentiles(chris_dB[josh_direct], pcts) if n_jd else None,
            "chris_direct":  percentiles(chris_dB[chris_direct], pcts) if n_cd else None,
            "ambiguous":     percentiles(chris_dB[ambig_band],  pcts) if n_am else None,
            "silence":       percentiles(chris_dB[silence],     pcts) if n_sl else None,
        },
        "single_channel_thresholds_josh_mic": {
            "T_high_above_is_josh_direct": T_high,
            "T_bleed_top_below_is_bleed":  T_bleed_top,
            "T_silence_below_is_silence":  T_silence,
            "overlap_zone": [overlap_lo, overlap_hi] if overlap_lo is not None else None,
            "interpretation": "RMS > T_high: Josh direct. T_silence < RMS < T_high: bleed (Chris or David, content fingerprints disambiguate). RMS < T_silence: silence.",
        },
    }
    out_path = ep_dir / "single_channel_calibration.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
