"""contextpulse_pipeline CLI — command-line entry point.

Usage:
    python -m contextpulse_pipeline.cli unify \\
        --session-id ep-2026-04-26-josh-cashman \\
        --bucket jerard-activefounder \\
        --speakers TX01=Josh,TX00=Chris,ambient=David \\
        --enhancements highpass,denoise,level_match,bleed_cancel

    python -m contextpulse_pipeline.cli validate-clusters \\
        --container ep-2026-04-26-josh-cashman \\
        --fingerprint-result fingerprint_result.json \\
        --unified-transcript unified_transcript_labeled.json \\
        --manifest episode_manifest.json \\
        --output cluster_identity_map.json

Subcommands:
    unify              Run Tier 1 audio unification pipeline for a session.
    validate-clusters  Map anonymous speaker_A/B/C clusters to manifest IDs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


def _parse_speakers(raw: str) -> dict[str, str]:
    """Parse 'TX01=Josh,TX00=Chris,ambient=David' into a dict."""
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise argparse.ArgumentTypeError(
                f"Invalid speaker mapping {pair!r}. Expected format: KEY=Name"
            )
        key, _, name = pair.partition("=")
        result[key.strip()] = name.strip()
    return result


def _parse_enhancements(raw: str) -> dict[str, bool]:
    """Parse 'highpass,denoise,level_match' into {highpass: True, denoise: True, ...}.

    Any key NOT present in the comma-list is set to False.
    """
    known = {"highpass", "denoise", "level_match", "bleed_cancel"}
    enabled = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = enabled - known
    if unknown:
        logger.warning("Unknown enhancement(s) ignored: %s", ", ".join(sorted(unknown)))
    return {k: (k in enabled) for k in known}


def cmd_unify(args: argparse.Namespace) -> int:
    """Execute the Tier 1 unify pipeline and print the MasterOutput JSON."""
    # Import here to keep startup fast for --help
    import boto3
    from contextpulse_pipeline.master import unify_audio

    speaker_mapping = _parse_speakers(args.speakers)
    enhancements = _parse_enhancements(args.enhancements) if args.enhancements else None

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )

    logger.info("Starting Tier 1 unification for session: %s", args.session_id)
    logger.info("Bucket: %s", args.bucket)
    logger.info("Speakers: %s", speaker_mapping)
    logger.info("Enhancements: %s", enhancements)

    s3_client = boto3.client("s3", region_name=args.region)

    result = unify_audio(
        session_id=args.session_id,
        s3_bucket=args.bucket,
        speaker_mapping=speaker_mapping,
        enhancements=enhancements,
        s3_client=s3_client,
    )

    output = {
        "audio_s3_uri": result.audio_s3_uri,
        "transcript_md_s3_uri": result.transcript_md_s3_uri,
        "transcript_json_s3_uri": result.transcript_json_s3_uri,
        "chapters_json_s3_uri": result.chapters_json_s3_uri,
        "qc_json_s3_uri": result.qc_json_s3_uri,
        "duration_sec": result.duration_sec,
        "speakers_detected": result.speakers_detected,
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_validate_clusters(args: argparse.Namespace) -> int:
    """Run cluster identity validation. Writes cluster_identity_map.json."""
    import json as _json
    from pathlib import Path

    from contextpulse_pipeline.cluster_validation import (
        EpisodeManifest,
        validate_cluster_identity,
        write_cluster_review_samples,
    )
    from contextpulse_pipeline.speaker_fingerprint import FingerprintResult
    from contextpulse_pipeline.unified_transcript import UnifiedTranscript

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )

    fp_path = Path(args.fingerprint_result)
    ut_path = Path(args.unified_transcript)
    manifest_path = Path(args.manifest)

    for label, path in [
        ("fingerprint-result", fp_path),
        ("unified-transcript", ut_path),
        ("manifest", manifest_path),
    ]:
        if not path.exists():
            logger.error("--%s file not found: %s", label, path)
            return 2

    logger.info("Loading fingerprint result from %s", fp_path)
    fp_result = FingerprintResult.from_json(path=fp_path)
    logger.info("Loading unified transcript from %s", ut_path)
    # UnifiedTranscript serializes via to_json; reload via JSON parse + reconstruct
    ut_data = _json.loads(ut_path.read_text(encoding="utf-8"))
    # Minimal reconstruction — we only need .segments[].text + .speaker_label
    # for content-only validation. UnifiedSegment.from_json isn't yet defined,
    # so build via dataclass directly.
    from datetime import datetime as _dt
    from contextpulse_pipeline.unified_transcript import UnifiedSegment

    segments = [
        UnifiedSegment(
            wall_start_utc=_dt.fromisoformat(s["wall_start_utc"]),
            wall_end_utc=_dt.fromisoformat(s["wall_end_utc"]),
            source_sha256=s["source_sha256"],
            source_filename=s.get("source_filename", ""),
            source_tier=s.get("source_tier", "?"),
            text=s.get("text", ""),
            avg_logprob=float(s.get("avg_logprob", 0.0)),
            speaker_label=s.get("speaker_label"),
        )
        for s in ut_data.get("segments", [])
    ]
    unified = UnifiedTranscript(
        container=ut_data.get("container", args.container),
        anchor_origination_utc=_dt.fromisoformat(ut_data["anchor_origination_utc"]),
        segments=segments,
        unreachable_sources=list(ut_data.get("unreachable_sources", [])),
        missing_transcripts=list(ut_data.get("missing_transcripts", [])),
    )
    logger.info("Loading episode manifest from %s", manifest_path)
    manifest = EpisodeManifest.from_json(manifest_path)

    logger.info(
        "Validating %d clusters against %d expected speakers",
        len(fp_result.clusters),
        len(manifest.expected_speakers),
    )
    identity_map = validate_cluster_identity(
        fingerprint_result=fp_result,
        unified_transcript=unified,
        manifest=manifest,
        container=args.container,
        enforce_unique=not args.no_enforce_unique,
    )

    output_path = Path(args.output) if args.output else Path("cluster_identity_map.json")
    identity_map.to_json(path=output_path)
    logger.info("Wrote identity map to %s", output_path)

    # Generate review samples for low-confidence clusters
    if identity_map.review_required and args.audio_dir:
        audio_dir = Path(args.audio_dir)
        audio_paths = {
            # Map sha256 -> path. Caller must name audio files <sha256_prefix>.<ext>
            # OR pass an explicit mapping JSON. For v1, scan directory.
            p.stem.split("_")[0]: p
            for p in audio_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".wav", ".ogg", ".mp3", ".m4a", ".flac"}
        }
        # The above heuristic is loose; better to require an explicit map.
        review_dir = output_path.parent / "cluster_review"
        written = write_cluster_review_samples(
            identity_map,
            fingerprint_result=fp_result,
            audio_paths=audio_paths,
            output_dir=review_dir,
        )
        if written:
            logger.info("Wrote %d cluster review samples to %s", len(written), review_dir)
            identity_map.to_json(path=output_path)  # re-write with review_audio_dir set

    # Summary
    summary: dict = {
        "container": identity_map.container,
        "validation_version": identity_map.validation_version,
        "n_clusters": len(identity_map.mappings),
        "n_review_required": len(identity_map.review_required),
        "mappings": [
            {
                "cluster": m.cluster_label,
                "speaker_id": m.speaker_id,
                "confidence": round(m.confidence, 3),
                "fingerprint_hits": m.fingerprint_hits[:5],  # truncate for stdout
            }
            for m in identity_map.mappings
        ],
        "review_required": identity_map.review_required,
        "output_path": str(output_path),
    }
    print(_json.dumps(summary, indent=2))

    # Exit code: 0 if all clusters resolved, 3 if any need review (caller can gate)
    return 0 if not identity_map.review_required else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m contextpulse_pipeline.cli",
        description="ContextPulse Pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- unify subcommand ---
    unify_parser = subparsers.add_parser(
        "unify",
        help="Run Tier 1 audio unification (ffmpeg concat + filter + mix + transcript merge).",
        description=(
            "Download per-channel OGG files from S3, apply Tier 1 filters, mix to mono MP3, "
            "merge transcripts, generate chapters, and upload all artifacts. Idempotent — "
            "skips expensive steps if outputs already exist in S3."
        ),
    )
    unify_parser.add_argument(
        "--session-id",
        required=True,
        help="Session identifier, e.g. ep-2026-04-26-josh-cashman. "
             "Used as S3 prefix under raw/<session-id>/dji/ and outputs/<session-id>/.",
    )
    unify_parser.add_argument(
        "--bucket",
        required=True,
        help="S3 bucket name, e.g. jerard-activefounder.",
    )
    unify_parser.add_argument(
        "--speakers",
        required=True,
        help="Comma-separated KEY=Name pairs mapping channel prefix to speaker name. "
             "Example: TX01=Josh,TX00=Chris,ambient=David",
    )
    unify_parser.add_argument(
        "--enhancements",
        default="highpass,denoise,level_match,bleed_cancel",
        help="Comma-separated list of Tier 1 enhancements to apply. "
             "Choices: highpass, denoise, level_match, bleed_cancel. "
             "Default: all enabled.",
    )
    unify_parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region for S3 client. Default: us-east-1.",
    )
    unify_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    unify_parser.set_defaults(func=cmd_unify)

    # --- validate-clusters subcommand ---
    vc_parser = subparsers.add_parser(
        "validate-clusters",
        help="Map anonymous Phase 1.5 clusters to manifest speaker IDs (content-only).",
        description=(
            "Take a FingerprintResult (Phase 1.5 output, anonymous speaker_A/B/C "
            "clusters) plus a labeled UnifiedTranscript and an episode manifest, "
            "and produce cluster_identity_map.json that maps each cluster to a "
            "real speaker_id. Clusters that score below 0.60 confidence go to "
            "REVIEW (exit code 3). Required gate before voice isolation + merge "
            "per the 2026-05-03 lesson."
        ),
    )
    vc_parser.add_argument(
        "--container",
        required=True,
        help="Container ID (episode slug), e.g. ep-2026-04-26-josh-cashman.",
    )
    vc_parser.add_argument(
        "--fingerprint-result",
        required=True,
        help="Path to fingerprint_result.json from Phase 1.5.",
    )
    vc_parser.add_argument(
        "--unified-transcript",
        required=True,
        help="Path to unified_transcript_labeled.json (after assign_speakers_to_unified).",
    )
    vc_parser.add_argument(
        "--manifest",
        required=True,
        help="Path to episode_manifest.json with expected_speakers + fingerprints.",
    )
    vc_parser.add_argument(
        "--output",
        default=None,
        help="Output path for cluster_identity_map.json. "
             "Defaults to ./cluster_identity_map.json.",
    )
    vc_parser.add_argument(
        "--audio-dir",
        default=None,
        help="Optional directory of source audio files. "
             "If set AND clusters need review, 10-sec WAV samples are written "
             "to <output_parent>/cluster_review/.",
    )
    vc_parser.add_argument(
        "--no-enforce-unique",
        action="store_true",
        help="Allow multiple clusters to map to the same speaker_id. "
             "Default behavior keeps the highest-confidence claimant and routes "
             "ties to REVIEW.",
    )
    vc_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    vc_parser.set_defaults(func=cmd_validate_clusters)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
