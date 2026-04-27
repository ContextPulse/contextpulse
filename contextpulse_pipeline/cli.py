"""contextpulse_pipeline CLI — command-line entry point.

Usage:
    python -m contextpulse_pipeline.cli unify \\
        --session-id ep-2026-04-26-josh-cashman \\
        --bucket jerard-activefounder \\
        --speakers TX01=Josh,TX00=Chris,ambient=David \\
        --enhancements highpass,denoise,level_match,bleed_cancel

Subcommands:
    unify   Run Tier 1 audio unification pipeline for a session.
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
