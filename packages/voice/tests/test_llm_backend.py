"""LLM backend for brief mode: the claude-cli route must be isolated and must say whether it ran."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from contextpulse_voice import llm_backend
from contextpulse_voice.llm_backend import LLMResult, complete, run_claude_cli


class FakeRun:
    """Stands in for subprocess.run and records what it was asked to run."""

    def __init__(self, stdout="", returncode=0, stderr="", exc=None):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, **kwargs):
        cwd = kwargs.get("cwd")
        self.calls.append({
            "cmd": list(cmd),
            "env": dict(kwargs.get("env") or {}),
            "cwd": cwd,
            "cwd_entries": sorted(os.listdir(cwd)) if cwd else None,
            "timeout": kwargs.get("timeout"),
            "stdin": kwargs.get("stdin", "INHERITED"),
        })
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def _cli_json(result="hello", is_error=False, model="claude-haiku-4-5"):
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result,
        "modelUsage": {model: {"inputTokens": 10}}, "duration_ms": 1234,
    })


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-child")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-never-reach-child")
    monkeypatch.setenv("CONTEXTPULSE_KEEP_ME", "1")


class TestClaudeCliIsolation:
    def test_child_env_has_no_anthropic_credentials(self, secret_env):
        fake = FakeRun(stdout=_cli_json())
        run_claude_cli("SYS", "Dictation:\nhi", exe="claude.exe", runner=fake)
        env = fake.calls[0]["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env.get("CONTEXTPULSE_KEEP_ME") == "1", "the rest of the environment must pass through"

    def test_parent_env_is_not_mutated(self, secret_env):
        run_claude_cli("SYS", "x", exe="claude.exe", runner=FakeRun(stdout=_cli_json()))
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-should-never-reach-child"

    def test_command_carries_the_isolation_flags(self):
        fake = FakeRun(stdout=_cli_json())
        run_claude_cli("SYS PROMPT", "Dictation:\nhi", model="haiku", exe="claude.exe", runner=fake)
        cmd = fake.calls[0]["cmd"]
        assert cmd[0] == "claude.exe"
        assert "-p" in cmd
        for flag, value in (("--setting-sources", "project"), ("--tools", ""),
                            ("--output-format", "json"), ("--model", "haiku"),
                            ("--system-prompt", "SYS PROMPT")):
            assert cmd[cmd.index(flag) + 1] == value, flag
        for flag in ("--strict-mcp-config", "--no-session-persistence",
                     "--exclude-dynamic-system-prompt-sections"):
            assert flag in cmd, flag

    def test_runs_from_an_empty_folder_that_is_removed_after(self):
        fake = FakeRun(stdout=_cli_json())
        run_claude_cli("SYS", "x", exe="claude.exe", runner=fake)
        call = fake.calls[0]
        assert call["cwd"] is not None
        assert call["cwd_entries"] == [], "cwd must be empty so no project context leaks in"
        assert not Path(call["cwd"]).exists(), "temp cwd must be cleaned up"

    def test_stdin_is_closed_so_the_cli_never_waits_on_an_inherited_pipe(self):
        fake = FakeRun(stdout=_cli_json())
        run_claude_cli("SYS", "x", exe="claude.exe", runner=fake)
        assert fake.calls[0]["stdin"] is subprocess.DEVNULL

    def test_timeout_is_passed_through(self):
        fake = FakeRun(stdout=_cli_json())
        run_claude_cli("SYS", "x", exe="claude.exe", runner=fake, timeout=12.5)
        assert fake.calls[0]["timeout"] == 12.5


class TestClaudeCliResults:
    def test_success_returns_text_and_model(self):
        r = run_claude_cli("S", "x", exe="claude.exe", runner=FakeRun(stdout=_cli_json("  out  ")))
        assert r == LLMResult(ok=True, text="out", backend="claude-cli",
                              model="claude-haiku-4-5", error="", duration_s=r.duration_s)

    @pytest.mark.parametrize("fake,fragment", [
        (FakeRun(returncode=1, stderr="boom"), "exit 1"),
        (FakeRun(stdout="not json"), "not JSON"),
        (FakeRun(stdout=_cli_json("Credit balance is too low", is_error=True)), "is_error"),
        (FakeRun(stdout=_cli_json("")), "empty"),
        (FakeRun(exc=subprocess.TimeoutExpired("claude", 30)), "timeout"),
        (FakeRun(exc=OSError("no such file")), "OSError"),
    ])
    def test_every_failure_reports_not_ok_with_a_reason(self, fake, fragment):
        r = run_claude_cli("S", "x", exe="claude.exe", runner=fake)
        assert r.ok is False
        assert r.text == ""
        assert r.backend == "claude-cli"
        assert fragment in r.error

    @pytest.mark.parametrize("shim", [r"C:\npm\claude.cmd", r"C:\x\CLAUDE.BAT"])
    def test_script_shim_is_refused_without_running(self, shim):
        fake = FakeRun(stdout=_cli_json())
        r = run_claude_cli("S", "x", exe=shim, runner=fake)
        assert r.ok is False and "shim" in r.error
        assert fake.calls == []

    def test_overlong_prompt_is_refused_without_running(self):
        fake = FakeRun(stdout=_cli_json())
        r = run_claude_cli("S", "x" * 30_000, exe="claude.exe", runner=fake)
        assert r.ok is False and "too long" in r.error
        assert fake.calls == []

    def test_missing_executable_reports_unavailable_without_running(self, monkeypatch):
        monkeypatch.setattr(llm_backend, "find_claude", lambda: None)
        fake = FakeRun(stdout=_cli_json())
        r = run_claude_cli("S", "x", runner=fake)
        assert r.ok is False and "not found" in r.error
        assert fake.calls == []


class TestComplete:
    def test_auto_prefers_cli_when_installed(self, monkeypatch):
        monkeypatch.setattr(llm_backend, "find_claude", lambda: "claude.exe")
        fake = FakeRun(stdout=_cli_json("cli"))
        r = complete("S", "x", backend="auto", runner=fake, api_key="")
        assert r.backend == "claude-cli" and r.text == "cli"

    def test_auto_falls_back_to_api_when_cli_absent_and_key_present(self, monkeypatch):
        monkeypatch.setattr(llm_backend, "find_claude", lambda: None)
        seen = {}

        def fake_api(system, user, *, model, api_key, timeout):
            seen["key"] = api_key
            return LLMResult(ok=True, text="api", backend="api")

        monkeypatch.setattr(llm_backend, "run_api", fake_api)
        r = complete("S", "x", backend="auto", api_key="k")
        assert r.text == "api" and seen["key"] == "k"

    def test_auto_with_nothing_available_is_not_ok(self, monkeypatch):
        monkeypatch.setattr(llm_backend, "find_claude", lambda: None)
        r = complete("S", "x", backend="auto", api_key="")
        assert r.ok is False and r.backend == "none"

    def test_unknown_backend_is_not_ok(self):
        r = complete("S", "x", backend="carrier-pigeon", api_key="")
        assert r.ok is False and "carrier-pigeon" in r.error

    def test_api_without_key_is_not_ok(self):
        r = complete("S", "x", backend="api", api_key="")
        assert r.ok is False and r.backend == "api"
