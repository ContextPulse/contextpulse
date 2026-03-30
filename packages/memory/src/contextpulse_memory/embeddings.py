"""Embedding engine using all-MiniLM-L6-v2 via ONNX runtime.

No PyTorch required — uses a pre-exported ONNX model with the tokenizers library.
Model auto-downloads on first use to ~/.contextpulse/models/minilm/.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import onnxruntime as ort
    from tokenizers import Tokenizer as _Tokenizer

logger = logging.getLogger("contextpulse.memory.embeddings")

# HuggingFace model repository (pre-exported ONNX via Optimum)
# Pinned to commit 10244843 — update _FILE_CHECKSUMS when bumping this hash.
_HF_COMMIT = "10244843eba3d9e479b27a4b81c94b56d8e9f4f2"
_HF_BASE = f"https://huggingface.co/optimum/all-MiniLM-L6-v2/resolve/{_HF_COMMIT}"
_MODEL_FILENAME = "model.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"
_MODEL_DIR = Path.home() / ".contextpulse" / "models" / "minilm"

EMBEDDING_DIM = 384


class EmbeddingEngine:
    """Thread-safe, lazy-loaded embedding engine using all-MiniLM-L6-v2 via ONNX.

    Use the module-level ``get_engine()`` to obtain the singleton instance.
    """

    def __init__(self) -> None:
        self._session: ort.InferenceSession | None = None
        self._tokenizer: _Tokenizer | None = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._available = False
        self._output_idx: int = 0  # index of last_hidden_state in session outputs

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """Load model and tokenizer on first call. Returns True when ready."""
        if self._available:
            return True
        with self._load_lock:
            if self._available:
                return True
            try:
                self._download_if_needed()
                self._load_model()
                self._available = True
                logger.info("EmbeddingEngine ready (all-MiniLM-L6-v2 ONNX, dim=%d)", EMBEDDING_DIM)
            except Exception:
                logger.exception(
                    "EmbeddingEngine failed to load — semantic search unavailable"
                )
                self._available = False
                # Delete cached model files so the next attempt re-downloads
                # (handles corrupted/truncated downloads)
                self._purge_cached_model()
            return self._available

    # Known-good SHA-256 digests for pinned commit 10244843.
    # Regenerate with: python -c "import hashlib,pathlib; ..."
    # Set to None to skip verification for a file.
    _FILE_CHECKSUMS: dict[str, str | None] = {
        _MODEL_FILENAME: "4a64cee3d4134bbdc86eed96e1a660efec58975417204ecfcf134140edb6e0e2",  # gitleaks:allow
        _TOKENIZER_FILENAME: "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",  # gitleaks:allow
    }

    @staticmethod
    def _sha256(path: Path) -> str:
        """Return lowercase hex SHA-256 of a file."""
        import hashlib
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _verify_checksum(self, filename: str, path: Path) -> None:
        """Verify file matches the expected SHA-256 checksum (if one is set)."""
        expected = self._FILE_CHECKSUMS.get(filename)
        if expected is None:
            return  # no checksum pinned — skip verification
        actual = self._sha256(path)
        if actual != expected.lower():
            path.unlink(missing_ok=True)
            raise ValueError(
                f"Checksum mismatch for {filename}: "
                f"expected {expected}, got {actual}. "
                "File deleted — will re-download on next use."
            )
        logger.debug("Checksum OK for %s", filename)

    def _download_if_needed(self) -> None:
        """Download model and tokenizer if not already cached."""
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        files = [
            (_MODEL_FILENAME, _MODEL_DIR / _MODEL_FILENAME),
            (_TOKENIZER_FILENAME, _MODEL_DIR / _TOKENIZER_FILENAME),
        ]
        for filename, dest in files:
            if dest.exists():
                # Verify existing file on startup (catches disk corruption)
                try:
                    self._verify_checksum(filename, dest)
                except ValueError as exc:
                    logger.warning("%s — re-downloading", exc)
                    # File was deleted by _verify_checksum; fall through to download
                else:
                    continue
            url = f"{_HF_BASE}/{filename}"
            logger.info("Downloading %s …", filename)
            tmp = dest.with_suffix(".tmp")
            try:
                urllib.request.urlretrieve(url, str(tmp))
                self._verify_checksum(filename, tmp)
                tmp.rename(dest)
                logger.info(
                    "Saved %s (%.1f KB)", filename, dest.stat().st_size / 1024
                )
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

    def _load_model(self) -> None:
        """Load ONNX inference session and tokenizer from disk."""
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = _MODEL_DIR / _MODEL_FILENAME
        tokenizer_path = _MODEL_DIR / _TOKENIZER_FILENAME

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.log_severity_level = 3  # suppress verbose ONNX runtime messages
        self._session = ort.InferenceSession(str(model_path), opts)

        # Locate the last_hidden_state output by name (falls back to index 0)
        output_names = [o.name for o in self._session.get_outputs()]
        self._output_idx = (
            output_names.index("last_hidden_state")
            if "last_hidden_state" in output_names
            else 0
        )

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=256)
        tokenizer.no_padding()
        self._tokenizer = tokenizer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the model is loaded and ready for inference."""
        return self._available

    def embed(self, text: str) -> list[float] | None:
        """Embed a single text string.

        Returns a 384-dimensional float list, or None if the model is unavailable.
        The returned vector is L2-normalised.
        """
        if not self._ensure_loaded():
            return None
        results = self._run_inference([text])
        return results[0] if results else None

    def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Embed multiple texts in one ONNX pass.

        Returns a list of 384-dimensional float lists, or None if unavailable.
        """
        if not texts:
            return []
        if not self._ensure_loaded():
            return None
        return self._run_inference(texts)

    def _run_inference(self, texts: list[str]) -> list[list[float]]:
        """Tokenize *texts*, run ONNX inference, mean-pool, and L2-normalise.

        Uses ``_infer_lock`` to prevent concurrent calls from interleaving
        the batch-padding enable/disable on the shared tokenizer.
        """
        assert self._tokenizer is not None
        assert self._session is not None

        with self._infer_lock:
            if len(texts) == 1:
                enc = self._tokenizer.encode(texts[0])
                input_ids = np.array([enc.ids], dtype=np.int64)
                attention_mask = np.array([enc.attention_mask], dtype=np.int64)
                token_type_ids = np.array([enc.type_ids], dtype=np.int64)
            else:
                # Pad batch to uniform length
                self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
                encodings = self._tokenizer.encode_batch(texts)
                self._tokenizer.no_padding()
                input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
                attention_mask = np.array(
                    [e.attention_mask for e in encodings], dtype=np.int64
                )
                token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

            # Build feed dict — only include token_type_ids if the model expects it
            input_names = {inp.name for inp in self._session.get_inputs()}
            feeds: dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in input_names:
                feeds["token_type_ids"] = token_type_ids

            outputs = self._session.run(None, feeds)

        hidden = outputs[self._output_idx].astype(np.float32)  # [B, T, 384]

        # Mean pooling over token dimension, weighted by attention mask
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)  # [B, T, 1]
        pooled = (hidden * mask).sum(axis=1) / (mask.sum(axis=1) + 1e-9)  # [B, 384]

        # L2 normalise
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalised = pooled / (norms + 1e-9)

        return normalised.tolist()

    def _purge_cached_model(self) -> None:
        """Delete cached model files so the next load attempt re-downloads."""
        for filename in (_MODEL_FILENAME, _TOKENIZER_FILENAME):
            path = _MODEL_DIR / filename
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        logger.info("Purged cached model files for re-download")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two embedding vectors.

        Assumes both vectors are already L2-normalised (as returned by embed()),
        in which case this reduces to a dot product.
        """
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        return float(np.dot(va, vb))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine_lock = threading.Lock()
_instance: EmbeddingEngine | None = None


def get_engine() -> EmbeddingEngine:
    """Return the module-level singleton ``EmbeddingEngine``.

    The engine is created lazily; the ONNX model is not loaded until the first
    call to ``embed()`` or ``embed_batch()``.
    """
    global _instance
    with _engine_lock:
        if _instance is None:
            _instance = EmbeddingEngine()
    return _instance


def _reset_for_testing() -> None:
    """Destroy the singleton so tests get a fresh instance.  Test-only."""
    global _instance
    with _engine_lock:
        _instance = None
