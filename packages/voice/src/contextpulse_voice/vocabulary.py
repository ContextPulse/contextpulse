"""Custom vocabulary replacement — fixes common Whisper misrecognitions.

Ported from Voiceasy with paths updated to ContextPulse voice directory.
Hot-reloads on file change — corrections take effect without restart.
"""

import json
import logging
import re
from pathlib import Path

from contextpulse_voice.config import VOCAB_FILE, LEARNED_VOCAB_FILE, VOICE_DATA_DIR

logger = logging.getLogger(__name__)

# Default vocabulary: common tech terms Whisper frequently mangles.
_PUNCTUATION: dict[str, str] = {
    "period": ".",
    "full stop": ".",
    "comma": ",",
    "exclamation point": "!",
    "exclamation mark": "!",
    "question mark": "?",
    "colon": ":",
    "semicolon": ";",
    "semi colon": ";",
    "dash": " —",
    "hyphen": "-",
    "open parenthesis": "(",
    "close parenthesis": ")",
    "open bracket": "[",
    "close bracket": "]",
    "ellipsis": "...",
    "dot dot dot": "...",
    "new line": "\n",
    "newline": "\n",
    "new paragraph": "\n\n",
}

_DEFAULT_VOCABULARY: dict[str, str] = {
    "cube control": "kubectl",
    "cube cuttle": "kubectl",
    "cube c t l": "kubectl",
    "cube CTL": "kubectl",
    "kubernetes": "Kubernetes",
    "kubernetties": "Kubernetes",
    "post gress": "PostgreSQL",
    "post gress q l": "PostgreSQL",
    "postgres q l": "PostgreSQL",
    "postgres": "PostgreSQL",
    "my sequel": "MySQL",
    "engine x": "nginx",
    "engine ex": "nginx",
    "fast a p i": "FastAPI",
    "fast api": "FastAPI",
    "pie torch": "PyTorch",
    "pi torch": "PyTorch",
    "num pie": "NumPy",
    "num pi": "NumPy",
    "get hub": "GitHub",
    "git hub": "GitHub",
    "local host": "localhost",
    "pie test": "pytest",
    "pi test": "pytest",
    "redis": "Redis",
    "redus": "Redis",
    "docker": "Docker",
    "docker file": "Dockerfile",
    "dev ops": "DevOps",
    "graphql": "GraphQL",
    "graph q l": "GraphQL",
    "type script": "TypeScript",
    "java script": "JavaScript",
    "react js": "ReactJS",
    "next js": "Next.js",
    "node js": "Node.js",
    "web socket": "WebSocket",
    "web sockets": "WebSockets",
}

# Module-level cache with file modification tracking for hot-reload.
_compiled_patterns: list[tuple[re.Pattern[str], str]] | None = None
_vocab_mtime: float = 0.0
_learned_mtime: float = 0.0


def _ensure_vocab_file() -> Path:
    """Create the vocabulary JSON file with defaults if it doesn't exist."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not VOCAB_FILE.exists():
        VOCAB_FILE.write_text(
            json.dumps(_DEFAULT_VOCABULARY, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Created default vocabulary file at %s", VOCAB_FILE)
    # Always create README if missing
    readme = VOICE_DATA_DIR / "vocabulary_README.txt"
    if not readme.exists():
        readme.write_text(
            "CONTEXTPULSE VOICE — CUSTOM VOCABULARY\n"
            "=======================================\n\n"
            "Edit vocabulary.json to add your own corrections.\n\n"
            "FORMAT:  \"what whisper hears\" : \"what you want\"\n\n"
            "EXAMPLE: If Whisper types 'Gerard' but you want 'Jerard',\n"
            "         add:  \"Gerard\": \"Jerard\"\n\n"
            "The LEFT side must match what Whisper actually outputs.\n"
            "To find out, dictate the word and check what appears.\n"
            "Then add a correction mapping that misspelling to the right word.\n\n"
            "Changes are picked up automatically — no restart needed.\n",
            encoding="utf-8",
        )
    return VOCAB_FILE


def _load_vocabulary() -> dict[str, str]:
    """Load the vocabulary dictionary from disk (user + learned)."""
    path = _ensure_vocab_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("vocabulary.json is not a JSON object — using defaults")
            data = dict(_DEFAULT_VOCABULARY)
        logger.info("Loaded %d user vocabulary entries from %s", len(data), path)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read vocabulary.json (%s) — using defaults", exc)
        data = dict(_DEFAULT_VOCABULARY)

    # Merge learned vocabulary (auto-discovered patterns)
    if LEARNED_VOCAB_FILE.exists():
        try:
            learned = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
            if isinstance(learned, dict):
                # User entries take priority over learned ones
                for key, val in learned.items():
                    if key not in data:
                        data[key] = val
                logger.info("Merged %d learned vocabulary entries", len(learned))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read vocabulary_learned.json: %s", exc)

    return data


def _compile_patterns(vocab: dict[str, str]) -> list[tuple[re.Pattern[str], str]]:
    """Pre-compile word-boundary regex patterns for each vocabulary entry."""
    patterns: list[tuple[re.Pattern[str], str]] = []
    # Sort by key length descending so longer phrases match first.
    for key in sorted(vocab, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE)
        patterns.append((pattern, vocab[key]))
    return patterns


def _get_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Return cached compiled patterns, auto-reloading when either file changes."""
    global _compiled_patterns, _vocab_mtime, _learned_mtime
    try:
        current_mtime = VOCAB_FILE.stat().st_mtime if VOCAB_FILE.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    try:
        current_learned = LEARNED_VOCAB_FILE.stat().st_mtime if LEARNED_VOCAB_FILE.exists() else 0.0
    except OSError:
        current_learned = 0.0
    if (_compiled_patterns is None
            or current_mtime != _vocab_mtime
            or current_learned != _learned_mtime):
        _compiled_patterns = _compile_patterns(_load_vocabulary())
        _vocab_mtime = current_mtime
        _learned_mtime = current_learned
        if _vocab_mtime > 0:
            logger.info("Vocabulary loaded (mtime=%.0f, learned_mtime=%.0f)",
                        _vocab_mtime, _learned_mtime)
    return _compiled_patterns


def reload_vocabulary() -> None:
    """Force-reload vocabulary from disk (call after editing the JSON file)."""
    global _compiled_patterns
    _compiled_patterns = _compile_patterns(_load_vocabulary())
    logger.info("Vocabulary reloaded")


def get_all_entries() -> dict[str, str]:
    """Return all vocabulary entries (user + learned). Used by MCP tools."""
    return _load_vocabulary()


def get_learned_entries() -> dict[str, str]:
    """Return only auto-learned entries. Used by MCP tools."""
    if not LEARNED_VOCAB_FILE.exists():
        return {}
    try:
        data = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _apply_punctuation(text: str) -> str:
    """Replace spoken punctuation words with actual punctuation characters."""
    for word, punct in sorted(_PUNCTUATION.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = re.compile(r"[,;:.!?\s]*\b" + re.escape(word) + r"\b[,;:.!?]*", re.IGNORECASE)
        text = pattern.sub(punct, text)
        pattern = re.compile(r"^" + re.escape(word) + r"\b", re.IGNORECASE)
        text = pattern.sub(punct, text)

    # Protect ellipsis from being mangled
    text = text.replace("...", "\x00ELLIPSIS\x00")

    # Clean up redundant punctuation
    text = re.sub(r"[,;]+([.!?:;\n])", r"\1", text)
    text = re.sub(r"([!?])[.,]+", r"\1", text)
    text = re.sub(r"\.([!?])", r"\1", text)
    text = re.sub(r"([.!?])[.,;:]+", r"\1", text)
    text = re.sub(r"([.!?:;,]) +([.!?:;,])", r"\2", text)

    # Restore ellipsis
    text = text.replace("\x00ELLIPSIS\x00", "...")

    # Add space after punctuation if missing
    text = re.sub(r"([.!?:;,])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"(\() ", r"\1", text)

    text = text.rstrip()
    text = re.sub(r"^ +", "", text)
    text = re.sub(r"\n ", "\n", text)
    return text


def apply_punctuation(text: str) -> str:
    """Replace spoken punctuation words with symbols (run BEFORE LLM cleanup)."""
    if not text:
        return text
    return _apply_punctuation(text)


def apply_vocabulary(text: str) -> str:
    """Replace known misrecognitions with correct terms (run AFTER LLM cleanup).

    This runs as the final pipeline step so the LLM cannot undo replacements.
    """
    if not text:
        return text
    for pattern, replacement in _get_patterns():
        text = pattern.sub(replacement, text)
    return text
