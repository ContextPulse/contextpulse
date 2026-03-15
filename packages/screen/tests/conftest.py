"""Shared fixtures for screen package tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add the screen package source to sys.path so we can import without installing
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# Mock heavy/platform-specific modules before any contextpulse imports
# These are not needed for unit testing pure logic

# Mock mss (Windows screen capture)
mock_mss = MagicMock()
sys.modules["mss"] = mock_mss

# Mock pynput (keyboard/mouse)
mock_pynput = MagicMock()
sys.modules["pynput"] = mock_pynput
sys.modules["pynput.keyboard"] = mock_pynput.keyboard

# Mock pystray (system tray)
mock_pystray = MagicMock()
sys.modules["pystray"] = mock_pystray

# Mock rapidocr_onnxruntime
mock_rapidocr = MagicMock()
sys.modules["rapidocr_onnxruntime"] = mock_rapidocr

# Mock mcp
mock_mcp = MagicMock()
sys.modules["mcp"] = mock_mcp
sys.modules["mcp.server"] = mock_mcp.server
sys.modules["mcp.server.fastmcp"] = mock_mcp.server.fastmcp

# Mock ctypes.windll for non-Windows or test environments
import ctypes
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()


@pytest.fixture
def tmp_buffer_dir(tmp_path):
    """Create a temporary buffer directory for tests."""
    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir()
    return buf_dir


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Create a temporary output directory for tests."""
    out_dir = tmp_path / "screenshots"
    out_dir.mkdir()
    return out_dir
