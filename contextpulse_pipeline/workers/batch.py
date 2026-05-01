# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""BatchPipeline — local-runnable, S3-backed batch transcription + synthesis worker.

This is the v0.1 Mode A (Batch) worker. It runs locally and requires an S3
bucket + Groq + Anthropic credentials. v0.2 will add SQS-driven spot workers
and Lambda integration.

Rules enforced:
- Rule #2: ConcurrentSynthesisError if synthesis already in flight
- Rule #3/#10: explicit episode on every manifest mutation
- Rule #9: published containers are immutable (via Manifest)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from contextpulse_pipeline.manifest import ContainerState, Manifest
from contextpulse_pipeline.synthesize import synthesize
from contextpulse_pipeline.transcribe import transcribe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConcurrentSynthesisError(Exception):
    """Raised when a synthesis pass is already in flight for this container.

    Enforces Rule #2: don't run synthesis multiple times in parallel.
    """


# ---------------------------------------------------------------------------
# BatchPipeline
# ---------------------------------------------------------------------------


class BatchPipeline:
    """Orchestrates ingest, transcription, and synthesis for one container.

    Usage:
        pipeline = BatchPipeline(
            container="ep-2026-04-26-josh-cashman",
            config=AF_CONFIG,
            s3_client=boto3.client("s3"),
            bucket="jerard-activefounder",
        )
        pipeline.ingest(audio_files)
        pipeline.finalize()
    """

    def __init__(
        self,
        container: str,
        config: dict[str, Any],
        s3_client: Any = None,
        bucket: str = "",
    ) -> None:
        self.container = container
        self.config = config
        self.s3_client = s3_client
        self.bucket = bucket

        self.manifest = Manifest(episode=container)
        self._synthesis_lock: bool = False

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, audio_files: list[Path]) -> Manifest:
        """Add audio files to the manifest, upload to S3, and transcribe.

        Rule #10: every audio file gets a manifest entry BEFORE processing.

        Args:
            audio_files: List of local audio file paths.

        Returns:
            The updated Manifest.
        """
        from contextpulse_pipeline.manifest import AudioEntry
        import datetime

        for audio_path in audio_files:
            sha256 = hashlib.sha256(audio_path.read_bytes()).hexdigest()

            # Create manifest entry FIRST (Rule #10)
            entry = AudioEntry(
                sha256=sha256,
                source_tier=self._infer_tier(audio_path),
                wall_start_utc=datetime.datetime.now(datetime.timezone.utc),
                duration_sec=0.0,  # duration populated post-transcription in v0.2
                file_path=str(audio_path),
            )
            self.manifest.add_audio(entry, episode=self.container)

            # Upload raw audio to S3 (if client available)
            if self.s3_client and self.bucket:
                raw_key = f"raw/{self.container}/{audio_path.name}"
                with audio_path.open("rb") as f:
                    self.s3_client.put_object(
                        Bucket=self.bucket,
                        Key=raw_key,
                        Body=f.read(),
                    )
                logger.info("Uploaded %s -> s3://%s/%s", audio_path.name, self.bucket, raw_key)

            # Transcribe (idempotent — skips if transcript already exists)
            if self.s3_client and self.bucket:
                transcript_key = transcribe(
                    audio_path,
                    episode=self.container,
                    manifest=self.manifest,
                    s3_client=self.s3_client,
                    bucket=self.bucket,
                )
                logger.info("Transcribed %s -> %s", audio_path.name, transcript_key)

        return self.manifest

    def _infer_tier(self, audio_path: Path) -> str:
        """Infer source tier from config tier_names or file extension."""
        tier_names: dict[str, str] = self.config.get("tier_names", {})
        suffix = audio_path.suffix.lower().lstrip(".")
        # Reverse lookup: find tier key whose value contains the suffix
        for tier_key, tier_label in tier_names.items():
            if suffix in tier_label.lower():
                return tier_key
        # Default: all unknown files are tier C
        return "C"

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(self) -> dict[str, str]:
        """Run partial synthesis (preview mode).

        Rule #2: raises ConcurrentSynthesisError if synthesis is in flight.

        Returns:
            Map of output_name -> S3 key.
        """
        return self._run_synthesis(partial=True)

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    def finalize(self) -> dict[str, str]:
        """Run canonical synthesis and transition container to 'finalized'.

        Rule #2: raises ConcurrentSynthesisError if synthesis is in flight.

        Returns:
            Map of output_name -> S3 key.
        """
        result = self._run_synthesis(partial=False)
        self.manifest.state = ContainerState.finalized
        logger.info("Container '%s' finalized.", self.container)
        return result

    # ------------------------------------------------------------------
    # Internal synthesis runner
    # ------------------------------------------------------------------

    def _run_synthesis(self, *, partial: bool) -> dict[str, str]:
        """Common synthesis runner with concurrent-lock guard (Rule #2)."""
        if self._synthesis_lock:
            raise ConcurrentSynthesisError(
                f"Synthesis is already in flight for container '{self.container}'. "
                "Wait for the current synthesis pass to complete. "
                "(Rule #2: no parallel synthesis for the same container.)"
            )

        prompt_set: dict[str, str] = self.config.get("synthesis_prompts", {})

        self._synthesis_lock = True
        try:
            return synthesize(
                self.manifest,
                prompt_set,
                self.s3_client,
                self.bucket,
                partial=partial,
            )
        finally:
            self._synthesis_lock = False
