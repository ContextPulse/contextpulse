# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Smoke tests for the phase1_5_fingerprint pipeline modules.

These tests don't hit AWS or load speechbrain — they verify:
1. Worker module imports cleanly (no syntax errors, no top-level fallout
   from missing speechbrain in the dev environment).
2. Submit module imports cleanly and exposes the expected CLI surface.
3. The user-data builder produces a string with the expected env-var name.
"""

from __future__ import annotations

import importlib


def test_worker_module_imports_cleanly() -> None:
    """The worker module must be importable without speechbrain installed.
    speechbrain is only loaded inside ECAPAExtractor.embed() at runtime.
    """
    mod = importlib.import_module("contextpulse_pipeline.pipelines.phase1_5_fingerprint.worker")
    assert hasattr(mod, "main")
    assert hasattr(mod, "_cross_platform_basename")
    # Sanity-check a known basename behavior we rely on (skill anti-pattern #14)
    assert mod._cross_platform_basename("C:\\Users\\david\\foo.mp3") == "foo.mp3"
    assert mod._cross_platform_basename("/tmp/audio/foo.mp3") == "foo.mp3"


def test_submit_module_imports_cleanly() -> None:
    mod = importlib.import_module("contextpulse_pipeline.pipelines.phase1_5_fingerprint.submit")
    assert hasattr(mod, "main")
    assert hasattr(mod, "upload_pipeline_code")
    assert hasattr(mod, "upload_inputs")
    assert hasattr(mod, "upload_spec")


def test_submit_cli_parser_has_required_args() -> None:
    """The CLI must require --raw-sources, --unified-transcript, --container,
    --output-dir. Argparse will exit(2) on missing-required, so we test by
    constructing the parser via importlib + monkey-running with --help that
    way the test stays hermetic."""
    mod = importlib.import_module("contextpulse_pipeline.pipelines.phase1_5_fingerprint.submit")
    # parse_args internally — invoke main() with empty argv and expect SystemExit
    import sys

    saved = sys.argv
    try:
        sys.argv = ["submit"]
        try:
            mod.main()
        except SystemExit as e:
            # argparse raises SystemExit(2) for missing required args
            assert e.code in (1, 2)
            return
    finally:
        sys.argv = saved
    raise AssertionError("Expected SystemExit on missing required CLI args")


def test_user_data_builder_includes_expected_envvars() -> None:
    """The user-data builder must reference both PHASE1_5_SPEC_S3_URI (for the
    worker) and the slim env file path (so the boot script can find it)."""
    mod = importlib.import_module("contextpulse_pipeline.pipelines.phase1_5_fingerprint.submit")
    user_data = mod._user_data(
        "s3://bucket/spec.json", "s3://bucket/code/infra/boot/boot_phase1_5_fingerprint.sh"
    )
    assert "PHASE1_5_SPEC_S3_URI=s3://bucket/spec.json" in user_data
    assert "/etc/cpp-phase1-5.env" in user_data
    assert "boot.sh" in user_data  # downloads boot script before exec
