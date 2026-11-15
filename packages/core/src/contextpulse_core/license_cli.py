# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Headless licence activation and status for the installed app.

The tray's "Enter License Key" dialog needs a desktop. A buyer on a clean
machine, a support script, or a test over SSM needs the same thing without
one, so the installed build exposes::

    ContextPulseCLI.exe --activate-license <file> [--json]
    ContextPulseCLI.exe --license-status [--json]

Activation reads the key from a FILE, never from the command line, so the key
does not land in shell history or in another process's view of this command
line. It calls ``license.save_license`` -- the exact function the dialog calls
-- so the verification (the embedded Ed25519 public key) and the file it writes
(``%APPDATA%\\ContextPulse\\license.key``) are the dialog's own.

Output never contains the key. The licence email is masked.

Exit codes:
    0  success (status printed, or key verified and saved)
    1  the key file could not be read
    2  usage error, empty file, or the key failed verification
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from contextpulse_core import license as _lic
from contextpulse_core.license import save_license

_FLAGS = ("--activate-license", "--license-status")


def handles(argv: list[str]) -> bool:
    """True if ``argv`` asks for one of this module's commands."""
    return any(a in _FLAGS for a in argv)


def mask_email(email: str) -> str:
    """``buyer@example.com`` -> ``b***@example.com``."""
    if not email:
        return ""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def _trial_days_remaining_readonly() -> int:
    """Days left in the trial WITHOUT starting it (a status query has no side effects)."""
    data = _lic._read_trial()
    if data is None:
        return _lic.TRIAL_DAYS
    elapsed = time.time() - data["start"]
    return max(_lic.TRIAL_DAYS - int(elapsed / 86400), 0)


def license_status() -> dict:
    """The installed app's licence state, as the Pro gate would see it."""
    payload = _lic.load_license()
    licensed = _lic.is_licensed()
    tier = payload.get("tier", "") if payload else ""
    trial_left = _trial_days_remaining_readonly()
    if licensed:
        pro_access = tier == "pro"
        source = "license"
    else:
        pro_access = trial_left > 0
        source = "trial" if pro_access else "none"
    exp = payload.get("exp") if payload else None
    if payload is None:
        expires = ""
    elif exp is None:
        expires = "never"
    else:
        expires = time.strftime("%Y-%m-%d", time.gmtime(exp))
    return {
        "licensed": licensed,
        "key_present": _lic.LICENSE_FILE.exists(),
        "tier": tier,
        "pro_access": pro_access,
        "source": source,
        "expires": expires,
        "email": mask_email(payload.get("email", "")) if payload else "",
        "trial_days_remaining": trial_left,
        "license_file": str(_lic.LICENSE_FILE),
    }


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, sort_keys=True))
    else:
        for k, v in data.items():
            if isinstance(v, bool):
                v = str(v).lower()
            print(f"{k}={v}")


def _read_key_file(path: Path) -> str:
    # utf-8-sig strips the BOM Notepad writes for "UTF-8 with BOM".
    return path.read_text(encoding="utf-8-sig").strip().lstrip("﻿").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ContextPulseCLI", description="ContextPulse licence tools")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--activate-license", metavar="FILE", help="verify and save the key in FILE")
    group.add_argument("--license-status", action="store_true", help="print the licence state")
    parser.add_argument("--json", action="store_true", help="print JSON instead of key=value lines")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors; return it instead
        return int(exc.code or 2)

    if args.license_status:
        _emit(license_status(), args.json)
        return 0

    path = Path(args.activate_license)
    try:
        key = _read_key_file(path)
    except OSError as exc:
        print(f"error: cannot read key file {path}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if not key:
        print(f"error: key file {path} is empty", file=sys.stderr)
        return 2
    payload = save_license(key)
    if payload is None:
        print("error: invalid licence key (signature check failed); nothing was saved", file=sys.stderr)
        return 2
    result = {"activated": True, **license_status()}
    _emit(result, args.json)
    return 0
