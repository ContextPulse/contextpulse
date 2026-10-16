# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""One call to a language model, by whichever route is available, that says whether it ran.

Two routes:

- ``claude-cli``: the local Claude Code CLI (``claude -p``), signed in to the
  user's own Claude plan. No API key and no per-call charge. The child process
  gets no ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, because the CLI prefers an
  API key in its environment over the signed-in plan and would bill it. It runs
  from an empty temporary folder with project-only settings, no tools and no
  MCP servers, so the user's hooks, CLAUDE.md files and MCP servers never see
  the dictation and nothing from the working folder leaks into the answer.
- ``api``: the Anthropic API with the configured key.

``auto`` uses the CLI when it is installed, otherwise the API when a key is set.

Every failure returns ``LLMResult(ok=False, error=...)`` rather than raising or
handing back the input, so a caller can always tell whether the model ran.
That is the defect this module exists to avoid: ``clean_with_llm`` returns its
input unchanged on four different failures, and its caller recorded
``cleanup_applied: True`` on all of them.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BACKENDS = ("auto", "claude-cli", "api")

# The CLI prefers either of these over the signed-in plan.
_STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Model aliases for the API route; the CLI accepts the alias itself.
_API_MODELS = {"haiku": "claude-haiku-4-5-20251001"}

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class LLMResult:
    ok: bool
    text: str
    backend: str
    error: str = ""
    model: str = ""
    duration_s: float = 0.0


def find_claude() -> str | None:
    """Full path of the Claude Code CLI, or None. Resolved, never a shell shim."""
    return shutil.which("claude")


def child_env() -> dict[str, str]:
    """A copy of this process's environment without Anthropic credentials."""
    env = dict(os.environ)
    for name in _STRIPPED_ENV:
        env.pop(name, None)
    return env


def build_cli_command(exe: str, system_prompt: str, user_text: str, model: str) -> list[str]:
    return [
        exe, "-p", user_text,
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "project",
        "--system-prompt", system_prompt,
        "--tools", "",
        "--strict-mcp-config",
        "--exclude-dynamic-system-prompt-sections",
    ]


def run_claude_cli(
    system_prompt: str,
    user_text: str,
    *,
    model: str = "haiku",
    timeout: float = 30.0,
    exe: str | None = None,
    runner: Runner = subprocess.run,
) -> LLMResult:
    """Run one prompt through ``claude -p``. Never raises."""
    exe = exe or find_claude()
    if not exe:
        return LLMResult(ok=False, text="", backend="claude-cli", error="claude CLI not found")

    start = time.monotonic()

    def fail(error: str) -> LLMResult:
        return LLMResult(ok=False, text="", backend="claude-cli", error=error,
                         duration_s=time.monotonic() - start)

    cmd = build_cli_command(exe, system_prompt, user_text, model)
    try:
        with tempfile.TemporaryDirectory(prefix="cp-llm-", ignore_cleanup_errors=True) as empty:
            # stdin=DEVNULL: `claude -p` also reads piped stdin and waits for its
            # EOF, so an inherited open pipe hangs the call until the timeout.
            proc = runner(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=child_env(), cwd=empty, timeout=timeout, stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        return fail(f"claude-cli timeout after {timeout:g}s")
    except OSError as exc:
        return fail(f"claude-cli OSError: {exc}")

    if proc.returncode != 0:
        # stderr can echo the prompt; log its length, not its text.
        return fail(f"claude-cli exit {proc.returncode} ({len(proc.stderr or '')} chars stderr)")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return fail("claude-cli output was not JSON")
    if not isinstance(payload, dict):
        return fail("claude-cli output was not JSON object")
    if payload.get("is_error"):
        return fail(f"claude-cli is_error: {str(payload.get('result', ''))[:200]}")
    text = str(payload.get("result") or "").strip()
    if not text:
        return fail("claude-cli returned empty result")
    usage = payload.get("modelUsage")
    reported_model = next(iter(usage), "") if isinstance(usage, dict) else ""
    return LLMResult(ok=True, text=text, backend="claude-cli", model=reported_model,
                     duration_s=time.monotonic() - start)


def run_api(
    system_prompt: str,
    user_text: str,
    *,
    model: str,
    api_key: str,
    timeout: float,
) -> LLMResult:
    """Run one prompt through the Anthropic API. Never raises."""
    if not api_key:
        return LLMResult(ok=False, text="", backend="api", error="no API key")
    start = time.monotonic()
    model_id = _API_MODELS.get(model, model)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        response = client.messages.create(
            model=model_id,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        text = response.content[0].text.strip()
    except Exception as exc:  # noqa: BLE001 -- any SDK or network failure is a not-ran result
        logger.warning("API call failed: %s", type(exc).__name__)
        return LLMResult(ok=False, text="", backend="api", error=f"api {type(exc).__name__}",
                         duration_s=time.monotonic() - start)
    if not text:
        return LLMResult(ok=False, text="", backend="api", error="api returned empty result",
                         duration_s=time.monotonic() - start)
    return LLMResult(ok=True, text=text, backend="api", model=model_id,
                     duration_s=time.monotonic() - start)


def complete(
    system_prompt: str,
    user_text: str,
    *,
    backend: str = "auto",
    model: str = "haiku",
    timeout: float = 30.0,
    api_key: str = "",
    runner: Runner = subprocess.run,
) -> LLMResult:
    """Send one prompt by the chosen route. Never raises; ``ok`` says whether it ran."""
    if backend not in BACKENDS:
        return LLMResult(ok=False, text="", backend="none", error=f"unknown backend {backend!r}")
    if backend == "auto":
        if find_claude():
            backend = "claude-cli"
        elif api_key:
            backend = "api"
        else:
            return LLMResult(ok=False, text="", backend="none",
                             error="no claude CLI and no API key")
    if backend == "claude-cli":
        return run_claude_cli(system_prompt, user_text, model=model, timeout=timeout, runner=runner)
    return run_api(system_prompt, user_text, model=model, api_key=api_key, timeout=timeout)
