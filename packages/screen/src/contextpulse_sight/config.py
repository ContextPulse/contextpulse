"""Configuration loading from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / "Projects" / ".env", override=True)  # global workspace credentials
load_dotenv(override=True)                        # local project overrides


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Output directory
OUTPUT_DIR = Path(_env("CONTEXTPULSE_OUTPUT_DIR", r"C:\Users\david\screenshots"))

# Image settings
MAX_WIDTH = int(_env("CONTEXTPULSE_MAX_WIDTH", "1280"))
MAX_HEIGHT = int(_env("CONTEXTPULSE_MAX_HEIGHT", "720"))
JPEG_QUALITY = max(1, min(100, int(_env("CONTEXTPULSE_JPEG_QUALITY", "85"))))

# Auto-capture interval in seconds (default 5s, 0 = disabled)
AUTO_INTERVAL = max(0, int(_env("CONTEXTPULSE_AUTO_INTERVAL", "5")))

# Rolling buffer settings
BUFFER_DIR = OUTPUT_DIR / "buffer"
BUFFER_MAX_AGE = max(0, int(_env("CONTEXTPULSE_BUFFER_MAX_AGE", "180")))  # seconds (3 min)
CHANGE_THRESHOLD = max(0.0, float(_env("CONTEXTPULSE_CHANGE_THRESHOLD", "1.5")))  # % pixel diff

# File paths (stable, overwritten each capture)
FILE_LATEST = OUTPUT_DIR / "screen_latest.png"
FILE_ALL = OUTPUT_DIR / "screen_all.png"
FILE_REGION = OUTPUT_DIR / "screen_region.png"

# Privacy: window title blocklist (case-insensitive substring matching)
BLOCKLIST_PATTERNS: list[str] = [
    p.strip() for p in _env("CONTEXTPULSE_BLOCKLIST", "").split(",") if p.strip()
]

_blocklist_file = Path(_env("CONTEXTPULSE_BLOCKLIST_FILE", ""))
if _blocklist_file.is_file():
    for _line in _blocklist_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            BLOCKLIST_PATTERNS.append(_line)
