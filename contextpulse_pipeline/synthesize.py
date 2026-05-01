# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Sonnet synthesis with prompt caching.

Uses Anthropic claude-sonnet-4-5 with prompt caching on the unified transcript.
Caching saves ~80% input tokens when running multiple prompts against the same
transcript.

Rule #8: write to S3 ONLY on full LLM success. On error, raise and let caller decide.
Rule #2: one synthesis pass per logical container — concurrent synthesis is blocked
         at the BatchPipeline level (ConcurrentSynthesisError).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from contextpulse_pipeline.manifest import Manifest, SynthesisRun

logger = logging.getLogger(__name__)

# Model constants
SYNTHESIS_MODEL = "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_unified_transcript(manifest: Manifest, s3_client: Any, bucket: str) -> str:
    """Build a unified transcript string from all non-superseded entries."""
    parts: list[str] = []
    for entry in manifest.audio_entries:
        if entry.superseded_by is not None:
            continue
        if entry.transcript_path is None:
            continue
        # Fetch transcript text from S3
        txt_key = entry.transcript_path.replace(".json", ".txt")
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=txt_key)
            text = obj["Body"].read().decode("utf-8")
        except Exception:
            # Fall back to JSON transcript
            try:
                obj = s3_client.get_object(Bucket=bucket, Key=entry.transcript_path)
                data = json.loads(obj["Body"].read())
                text = data.get("text", "")
            except Exception:
                logger.warning("Could not fetch transcript for entry %s", entry.sha256[:12])
                continue
        label = f"[{entry.participant or entry.source_tier} - {entry.wall_start_utc.isoformat()}]"
        parts.append(f"{label}\n{text}")
    return "\n\n".join(parts)


def _call_anthropic(
    transcript: str,
    prompt_set: dict[str, str],
) -> dict[str, str]:
    """Call Anthropic with prompt caching. Returns dict of output_name -> text.

    The transcript is sent as a cached system block, shared across all prompts.
    """
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("anthropic package not installed. Run: uv add anthropic") from exc

    client = anthropic.Anthropic()
    results: dict[str, str] = {}

    # Shared cached system content — one API call per prompt but transcript cached
    system_content = [
        {
            "type": "text",
            "text": (
                "You are a thoughtful analyst. Below is a full transcript. "
                "Answer the user's question based only on this transcript.\n\n"
                f"<transcript>\n{transcript}\n</transcript>"
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for output_name, user_prompt in prompt_set.items():
        logger.info("Running synthesis prompt: %s", output_name)
        response = client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=4096,
            system=system_content,
            messages=[{"role": "user", "content": user_prompt}],
        )
        results[output_name] = response.content[0].text

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthesize(
    manifest: Manifest,
    prompt_set: dict[str, str],
    s3_client: Any,
    bucket: str,
    *,
    partial: bool = False,
) -> dict[str, str]:
    """Run LLM synthesis on all non-superseded transcripts.

    Rule #8: writes to S3 ONLY after ALL prompts succeed. On any error,
    raises immediately so the caller (BatchPipeline) can decide what to do.

    Args:
        manifest: The container manifest.
        prompt_set: Map of output_name -> prompt string.
        s3_client: Boto3 S3 client.
        bucket: S3 bucket name.
        partial: If True, marks the SynthesisRun as partial (preview mode).

    Returns:
        Map of output_name -> S3 key.
    """
    episode = manifest.episode

    # Build unified transcript from non-superseded entries
    transcript = _build_unified_transcript(manifest, s3_client, bucket)
    if not transcript.strip():
        raise ValueError(
            f"No transcript content found for episode '{episode}'. "
            "Ensure transcription has completed before calling synthesize()."
        )

    # Determine which tier produced the primary content
    active_tiers = {
        e.source_tier for e in manifest.audio_entries if e.superseded_by is None and e.transcript_path
    }
    tier_used = ",".join(sorted(active_tiers)) if active_tiers else "unknown"

    # Call Anthropic (raises on error — Rule #8)
    raw_outputs = _call_anthropic(transcript, prompt_set)

    # Only write to S3 after ALL prompts succeed (Rule #8)
    output_keys: dict[str, str] = {}
    for output_name, content in raw_outputs.items():
        key = f"outputs/{episode}/{output_name}.md"
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content.encode("utf-8"),
        )
        output_keys[output_name] = key
        logger.info("Wrote synthesis output: s3://%s/%s", bucket, key)

    # Record synthesis run in manifest
    run = SynthesisRun(
        type="preview" if partial else "finalize",
        at=datetime.now(timezone.utc),
        tier_used=tier_used,
        outputs=list(output_keys.values()),
        partial=partial,
    )
    manifest.record_synthesis_run(run)

    return output_keys
