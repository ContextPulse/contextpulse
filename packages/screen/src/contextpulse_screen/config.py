"""Configuration loading from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Output directory
OUTPUT_DIR = Path(_env("CONTEXTPULSE_OUTPUT_DIR", r"C:\Users\david\screenshots"))

# Image settings
MAX_WIDTH = int(_env("CONTEXTPULSE_MAX_WIDTH", "1280"))
MAX_HEIGHT = int(_env("CONTEXTPULSE_MAX_HEIGHT", "720"))
JPEG_QUALITY = int(_env("CONTEXTPULSE_JPEG_QUALITY", "85"))

# Auto-capture interval in seconds (default 5s, 0 = disabled)
AUTO_INTERVAL = int(_env("CONTEXTPULSE_AUTO_INTERVAL", "5"))

# Rolling buffer settings
BUFFER_DIR = OUTPUT_DIR / "buffer"
BUFFER_MAX_AGE = int(_env("CONTEXTPULSE_BUFFER_MAX_AGE", "180"))  # seconds (3 min)
CHANGE_THRESHOLD = float(_env("CONTEXTPULSE_CHANGE_THRESHOLD", "1.5"))  # % pixel diff

# File paths (stable, overwritten each capture)
FILE_LATEST = OUTPUT_DIR / "screen_latest.png"
FILE_ALL = OUTPUT_DIR / "screen_all.png"
FILE_REGION = OUTPUT_DIR / "screen_region.png"
