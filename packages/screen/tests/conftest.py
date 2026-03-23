"""Shared fixtures for screen package tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add the screen package source to sys.path so we can import without installing
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# Save originals before mocking so we can restore them after screen tests
_MOCKED_MODULES = ["mss", "pynput", "pynput.keyboard", "pystray", "rapidocr_onnxruntime"]
_original_modules = {k: sys.modules.get(k) for k in _MOCKED_MODULES}

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

# Mock ctypes.windll for non-Windows or test environments
import ctypes
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()


@pytest.fixture(scope="session", autouse=True)
def restore_mocked_modules():
    """Restore mocked sys.modules entries after screen tests complete.

    Module-level mocks persist across test files in the same process. This
    fixture cleans them up so subsequent packages (memory, project) see the
    real modules rather than MagicMock stubs.
    """
    yield
    for mod_name, original in _original_modules.items():
        if original is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = original


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
