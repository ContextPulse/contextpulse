# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Windows version resource for the frozen ContextPulse executables.

Without one, Explorer's Properties > Details shows blank File version and
Product version on ContextPulse.exe, and only the uninstall entry says which
release is installed (found 2026-11-15 by the clean-machine installer test).

contextpulse.spec loads this file by path and passes the result to EXE(
version=...). The version number is read from contextpulse_core.__version__,
the same string the MCP server reports, so the two cannot disagree.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_INIT = REPO_ROOT / "packages" / "core" / "src" / "contextpulse_core" / "__init__.py"

COMPANY = "Jerard Ventures LLC"
PRODUCT = "ContextPulse"
COPYRIGHT = "Copyright (C) 2025-2026 Jerard Ventures LLC. AGPL-3.0-or-later."


def read_version(init_path: Path = CORE_INIT) -> str:
    """Return __version__ from contextpulse_core without importing the package."""
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_path.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise ValueError(f"no __version__ in {init_path}")
    return match.group(1)


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """'0.1.2' -> (0, 1, 2, 0). A pre-release suffix ('0.2.0rc1') is dropped."""
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", version)
    if match is None:
        raise ValueError(f"not a version number: {version!r}")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def build_version_info(exe_name: str, description: str, version: str | None = None):
    """Return a PyInstaller VSVersionInfo for one executable."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version = version or read_version()
    numbers = version_tuple(version)
    strings = [
        StringStruct("CompanyName", COMPANY),
        StringStruct("FileDescription", description),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", exe_name),
        StringStruct("LegalCopyright", COPYRIGHT),
        StringStruct("OriginalFilename", f"{exe_name}.exe"),
        StringStruct("ProductName", PRODUCT),
        StringStruct("ProductVersion", version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),  # en-US, Unicode
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
