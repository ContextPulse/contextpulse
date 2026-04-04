"""Whisper model manager — downloads model on first run with progress.

Whisper model download and management.
"""

import logging
import os
import sys
from pathlib import Path

from contextpulse_voice.config import MODEL_DIR

logger = logging.getLogger(__name__)


def get_model_path(model_size: str = "base") -> str:
    """Return the model path, downloading if necessary.

    For development (unfrozen), uses the default HuggingFace cache.
    For frozen EXE, downloads to %APPDATA%/ContextPulse/voice/models/.
    """
    if not getattr(sys, "frozen", False):
        # Development mode — let faster-whisper use its default cache
        return model_size

    # Frozen EXE mode — manage our own model directory
    model_dir = MODEL_DIR / f"faster-whisper-{model_size}"
    model_file = model_dir / "model.bin"

    if model_file.exists():
        logger.info("Model found at %s", model_dir)
        return str(model_dir)

    # Need to download
    logger.info("Downloading Whisper '%s' model to %s ...", model_size, model_dir)
    _download_model(model_size, model_dir)
    return str(model_dir)


def _download_model(model_size: str, target_dir: Path) -> None:
    """Download the faster-whisper model from HuggingFace."""
    try:
        # Suppress tqdm progress bars in frozen EXE (no console to write to)
        if getattr(sys, "frozen", False):
            os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

        from huggingface_hub import snapshot_download

        repo_id = f"Systran/faster-whisper-{model_size}"
        target_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading %s (this may take a few minutes on first run)...", repo_id)
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )
        logger.info("Model download complete: %s", target_dir)
    except Exception:
        logger.exception("Failed to download model")
        raise RuntimeError(
            f"Could not download Whisper '{model_size}' model. "
            "Check your internet connection and try again."
        )
