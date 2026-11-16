# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""The frozen executables must carry a Windows version resource.

Found 2026-11-15 by the clean-machine installer test: ContextPulse.exe 0.1.2
had empty FileVersion and ProductVersion, so nothing on the file itself said
which release was installed.
"""

from __future__ import annotations

import ctypes
import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "contextpulse.spec"


def _load():
    spec = importlib.util.spec_from_file_location(
        "windows_version_info", REPO_ROOT / "scripts" / "windows_version_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vi = _load()


def test_version_matches_the_installer_default():
    """__version__, which the MCP server reports, and installer.iss agree."""
    iss = (REPO_ROOT / "installer.iss").read_text(encoding="utf-8")
    default = re.search(r'#define MyAppVersion "([^"]+)"', iss).group(1)
    assert vi.read_version() == default


@pytest.mark.parametrize("text,expected", [
    ("0.1.2", (0, 1, 2, 0)),
    ("1.0", (1, 0, 0, 0)),
    ("0.2.0rc1", (0, 2, 0, 0)),
    ("10.20.30.40", (10, 20, 30, 40)),
])
def test_version_tuple(text, expected):
    assert vi.version_tuple(text) == expected


def test_version_tuple_rejects_garbage():
    with pytest.raises(ValueError):
        vi.version_tuple("latest")


def test_spec_gives_both_executables_a_version_resource():
    text = SPEC_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"\bEXE\((.*?)\n\)", text, re.S)
    assert len(blocks) == 2, "expected ContextPulse and ContextPulseCLI EXE blocks"
    for block in blocks:
        assert re.search(r"^\s*version=", block, re.M), block


def _file_version_strings(path: Path) -> dict[str, str]:
    """Read the version strings back with Windows' own API, not PyInstaller's."""
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    assert size, "no version resource"
    buf = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, buf)
    out = {}
    for key in ("FileVersion", "ProductVersion", "ProductName", "CompanyName"):
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if version.VerQueryValueW(buf, f"\\StringFileInfo\\040904B0\\{key}",
                                  ctypes.byref(ptr), ctypes.byref(length)):
            out[key] = ctypes.wstring_at(ptr.value, length.value).rstrip("\x00")
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version resources")
def test_stamped_executable_reports_the_version(tmp_path):
    pytest.importorskip("win32api")
    pytest.importorskip("pefile")
    from PyInstaller.utils.win32.versioninfo import write_version_info_to_executable

    # The venv's launcher is a real PE file; stamp a copy, never the original.
    exe = tmp_path / "ContextPulse.exe"
    shutil.copyfile(sys.executable, exe)
    write_version_info_to_executable(str(exe), vi.build_version_info("ContextPulse", "ContextPulse",
                                                                     version="0.1.2"))
    strings = _file_version_strings(exe)
    assert strings["FileVersion"] == "0.1.2"
    assert strings["ProductVersion"] == "0.1.2"
    assert strings["ProductName"] == "ContextPulse"
    assert strings["CompanyName"] == "Jerard Ventures LLC"
