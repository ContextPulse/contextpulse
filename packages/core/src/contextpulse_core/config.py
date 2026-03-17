"""Shared configuration utilities for all ContextPulse packages."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / "Projects" / ".env", override=True)  # global workspace credentials
load_dotenv(override=True)                        # local project overrides


def env(key: str, default: str) -> str:
    """Read an environment variable with a default."""
    return os.environ.get(key, default)


# Platform-wide paths
CONTEXTPULSE_HOME = Path(env("CONTEXTPULSE_HOME", str(Path.home() / ".contextpulse")))
