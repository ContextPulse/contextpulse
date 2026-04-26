"""ActiveFounder integration example.

Shows how the AF hike pipeline would consume contextpulse_pipeline.
This is a reference, not production code -- AF will adopt this pattern in
its own codebase as part of Phase 3 v0.2 deployment.

The AF pipeline converts 3.5-hour hike recordings (multi-mic, multi-speaker)
into structured documents: storyline, business plan, competitive landscape,
persona evaluation, and idea name candidates.

Audio source tier hierarchy:
  A = DJI lavalier WAV (broadcast quality, highest tier)
  B = iPhone Voice Memo m4a (direct device recording)
  C = Telegram-uploaded MP3 (transport-compressed, lowest quality)
"""

from __future__ import annotations

from pathlib import Path

from contextpulse_pipeline import BatchPipeline, AudioSourceTier

AF_CONFIG: dict = {
    # The AF domain calls its containers "episodes"
    "container_term": "episode",
    # Tier definitions map tier keys to recognizable file patterns
    "tier_names": {
        "A": "dji-wav",
        "B": "iphone-direct",
        "C": "telegram-mp3",
    },
    # Synthesis prompts -- AF uses these four structured outputs per episode
    "synthesis_prompts": {
        "storyline": (
            "Write a compelling narrative storyline of this conversation, "
            "preserving the key arc, emotional moments, and insights discussed. "
            "Format as flowing prose, 600-1000 words."
        ),
        "business_plan": (
            "Extract all business ideas, venture concepts, and strategic thinking "
            "from the conversation. For each idea: name, problem, solution, market, "
            "differentiation, and next step. Format as structured markdown sections."
        ),
        "competitive_landscape": (
            "Identify all competitors, analogues, and market players mentioned or "
            "implied. For each: name, what they do, perceived strengths/weaknesses, "
            "and how the discussed ideas compare. Format as a comparison table followed "
            "by analysis paragraphs."
        ),
        "persona_evaluation": (
            "Identify the target customer personas discussed in this conversation. "
            "For each persona: demographics, pain points, buying triggers, objections, "
            "and fit with the ideas discussed. Format as structured persona cards."
        ),
    },
    # Diarization mode -- ambient-only means only the room mic / bleed-through
    # gets diarized; each lavalier channel is treated as a dedicated speaker
    "diarization": "ambient-only",
    # Participant strategy: spoken declaration in first 60s takes priority;
    # falls back to LLM inference if no declaration is found
    "participant_strategy": "spoken-announcement-then-llm-fallback",
}


def run_hike_pipeline(episode_slug: str, audio_files: list[Path]) -> None:
    """Run the full ActiveFounder hike pipeline for one episode.

    Args:
        episode_slug: Unique identifier for this episode (e.g. "ep-2026-04-26-josh-cashman").
        audio_files: Local audio file paths from all recording sources.
                     Mix of DJI WAV, iPhone m4a, and Telegram MP3 is fine --
                     tier supersession handles priority automatically.

    Example:
        run_hike_pipeline(
            "ep-2026-04-26-josh-cashman",
            list(Path("D:/DJI").glob("*.wav")) + list(Path("~/Downloads").glob("*.m4a")),
        )

    What happens:
        1. Each audio file gets a manifest entry before processing
        2. Files are uploaded to S3 raw/ep-.../
        3. Files >25 MB are auto-compressed to opus before Groq Whisper
        4. Transcription is idempotent -- re-runs skip already-transcribed files
        5. Tier-A files supersede Tier-B/C files for the same time window
        6. synthesize() runs four Sonnet prompts with the transcript cached
        7. Container state transitions to "finalized"
    """
    pipeline = BatchPipeline(
        container=episode_slug,
        config=AF_CONFIG,
        bucket="jerard-activefounder",
        # s3_client defaults to None -- in production, pass boto3.client("s3")
    )
    pipeline.ingest(audio_files)
    pipeline.finalize()


# ---------------------------------------------------------------------------
# Phase 3 deployment notes (for the AF session that will adopt this)
# ---------------------------------------------------------------------------
# 1. AF will call run_hike_pipeline() from the Telegram bot's /finalize handler
# 2. audio_files will be collected from the episode's S3 raw-staging/ prefix
# 3. s3_client should be passed in from AF's AWS session
# 4. The bucket "jerard-activefounder" must exist and have the AF IAM role
#    with s3:PutObject + s3:GetObject + s3:HeadObject permissions
# 5. AF's existing Step Functions synthesis architecture should be RETIRED
#    once this pipeline is validated on 3 real hikes (Rule #2 fix)
# 6. The "participant_strategy" key is read by the diarization layer (v0.2+)
#    -- in v0.1 batch mode it is stored in config but not yet acted on
