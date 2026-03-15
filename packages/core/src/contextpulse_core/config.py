"""Shared configuration utilities for all ContextPulse packages."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)


def env(key: str, default: str) -> str:
    """Read an environment variable with a default."""
    return os.environ.get(key, default)


# Platform-wide paths
CONTEXTPULSE_HOME = Path(env("CONTEXTPULSE_HOME", str(Path.home() / ".contextpulse")))
