# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for local bearer-token auth on the unified MCP HTTP endpoint.

Written before contextpulse_core.mcp_auth existed, per the spec's step order
(.internal/audit-2026-09-19/spec-mcp-local-auth.md section 6, step 3).

The fixture deliberately wraps a REAL FastMCP streamable-http app -- not a
stand-in -- and reuses the production TransportSecuritySettings builder from
mcp_unified rather than a hand-copied dict, so a change to the production
settings shows up here instead of drifting silently.
"""

import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from contextpulse_core import mcp_auth, mcp_unified
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

pytestmark = pytest.mark.unit

PORT = 8420
BASE_URL = f"http://127.0.0.1:{PORT}"
RPC_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
TOKEN = "test-token-not-a-real-one-0123456789abcdef"


def _dummy_fastmcp() -> FastMCP:
    """A throwaway FastMCP carrying the production transport security settings."""
    app = FastMCP(
        "t",
        host="127.0.0.1",
        port=PORT,
        stateless_http=True,
        json_response=True,
        transport_security=mcp_unified.build_transport_security(PORT),
    )

    @app.tool()
    def dummy_probe() -> str:
        """A tool that exists only so tools/list has something to return."""
        return "ok"

    return app


@pytest.fixture
def auth_client():
    """TestClient over BearerAuthASGI(FastMCP.streamable_http_app())."""
    app = mcp_auth.BearerAuthASGI(_dummy_fastmcp().streamable_http_app(), TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


def _post(client, headers=None):
    merged = dict(RPC_HEADERS)
    if headers:
        merged.update(headers)
    return client.post("/mcp", json=RPC_TOOLS_LIST, headers=merged)


# ── 1-3: the core 401/401/200 ladder ─────────────────────────────────

def test_missing_authorization_header_is_401(auth_client):
    resp = _post(auth_client)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")
    assert resp.json()["error"] == "invalid_token"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token",
        "Bearer ",
        "bearer ",
        "Bearer",
        f"Basic {TOKEN}",
        f"Token {TOKEN}",
        TOKEN,  # bare token, no scheme
        "",
    ],
)
def test_bad_authorization_header_is_401(auth_client, header):
    resp = _post(auth_client, {"Authorization": header})
    assert resp.status_code == 401, f"{header!r} was accepted"
    assert resp.json()["error"] == "invalid_token"


def test_correct_token_is_200_and_lists_tools(auth_client):
    resp = _post(auth_client, {"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200, resp.text
    names = [t["name"] for t in resp.json()["result"]["tools"]]
    assert "dummy_probe" in names


def test_bearer_scheme_is_case_insensitive(auth_client):
    """RFC 7235 makes the auth scheme case-insensitive; clients do vary."""
    resp = _post(auth_client, {"Authorization": f"bearer {TOKEN}"})
    assert resp.status_code == 200, resp.text


# ── 4-5: the library's Host/Origin layer, and the ordering ───────────

def test_valid_token_with_foreign_origin_is_403(auth_client):
    resp = _post(
        auth_client,
        {"Authorization": f"Bearer {TOKEN}", "Origin": "http://evil.example"},
    )
    assert resp.status_code == 403


def test_valid_token_with_foreign_host_is_421(auth_client):
    resp = _post(
        auth_client,
        {"Authorization": f"Bearer {TOKEN}", "Host": "evil.example:8420"},
    )
    assert resp.status_code == 421


def test_localhost_origin_is_allowed(auth_client):
    """Positive control for the 403 above -- proves 403 is about the Origin."""
    resp = _post(
        auth_client,
        {"Authorization": f"Bearer {TOKEN}", "Origin": f"http://localhost:{PORT}"},
    )
    assert resp.status_code == 200, resp.text


def test_no_token_with_foreign_origin_is_401_not_403(auth_client):
    """Ordering: the bearer wrapper is outermost, so auth decides first."""
    resp = _post(auth_client, {"Origin": "http://evil.example"})
    assert resp.status_code == 401


# ── 6: token file lifecycle ──────────────────────────────────────────

def test_load_or_create_token_creates_then_reuses(tmp_path):
    target = tmp_path / "mcp_token"
    first = mcp_auth.load_or_create_token(target)
    assert len(first) >= 43
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == first
    assert mcp_auth.load_or_create_token(target) == first


def test_load_or_create_token_is_race_safe_across_threads(tmp_path):
    target = tmp_path / "mcp_token"
    results: list[str] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def worker():
        try:
            start.wait(timeout=5)
            results.append(mcp_auth.load_or_create_token(target))
        except BaseException as exc:  # noqa: BLE001 - recorded and re-asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    assert len(results) == 8
    assert len(set(results)) == 1, f"threads disagreed on the token: {set(results)}"
    assert target.read_text(encoding="utf-8").strip() == results[0]
    assert not list(tmp_path.glob("*.tmp")), "temp files left behind"


def test_empty_token_file_raises_rather_than_returning_empty(tmp_path):
    """A zero-byte token file must fail loudly, never authenticate everyone."""
    target = tmp_path / "mcp_token"
    target.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        mcp_auth.load_or_create_token(target)


# ── 7: file permissions, both platforms ──────────────────────────────

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_token_file_is_0600_on_posix(tmp_path):
    target = tmp_path / "mcp_token"
    mcp_auth.load_or_create_token(target)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
def test_token_file_acl_is_user_only_on_windows(tmp_path):
    target = tmp_path / "mcp_token"
    mcp_auth.load_or_create_token(target)

    out = subprocess.run(
        ["icacls", str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    aces = mcp_auth.parse_icacls_aces(out, target)

    assert len(aces) == 1, f"expected exactly one ACE, got {aces}"
    assert os.environ["USERNAME"].lower() in aces[0].lower(), aces
    for bad in ("(I)", "BUILTIN\\Users", "Everyone", "Authenticated Users"):
        assert all(bad.lower() not in ace.lower() for ace in aces), f"{bad} in {aces}"


def test_parse_icacls_aces_handles_real_output_shape():
    """Frozen real icacls output -- the parser must not depend on line count."""
    path = Path(r"C:\Users\someone\AppData\Roaming\ContextPulse\mcp_token")
    inherited = (
        f"{path} NT AUTHORITY\\SYSTEM:(I)(F)\n"
        "               BUILTIN\\Administrators:(I)(F)\n"
        "               CORSAIRAI\\someone:(I)(F)\n"
        "\n"
        "Successfully processed 1 files; Failed processing 0 files\n"
    )
    restricted = (
        f"{path} CORSAIRAI\\someone:(F)\n"
        "\n"
        "Successfully processed 1 files; Failed processing 0 files\n"
    )
    assert len(mcp_auth.parse_icacls_aces(inherited, path)) == 3
    assert mcp_auth.parse_icacls_aces(restricted, path) == ["CORSAIRAI\\someone:(F)"]


# ── 8: the off switch ────────────────────────────────────────────────

def test_auth_off_serves_unauthenticated_and_warns(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", "off")
    caplog.set_level("WARNING")
    app = mcp_unified.build_http_app(_dummy_fastmcp(), token_file=tmp_path / "mcp_token")
    with TestClient(app, base_url=BASE_URL) as client:
        resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert any("MCP AUTH DISABLED" in r.message for r in caplog.records)
    assert not (tmp_path / "mcp_token").exists(), "no token should be created when off"


def test_no_auth_flag_serves_unauthenticated_and_warns(caplog, tmp_path):
    caplog.set_level("WARNING")
    app = mcp_unified.build_http_app(
        _dummy_fastmcp(), no_auth=True, token_file=tmp_path / "mcp_token"
    )
    with TestClient(app, base_url=BASE_URL) as client:
        resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert any("MCP AUTH DISABLED" in r.message for r in caplog.records)


@pytest.mark.parametrize("value", ["", "0", "false", "no", "OFF ", "on", "disabled"])
def test_only_the_literal_off_disables_auth(monkeypatch, value, tmp_path):
    """Fail closed: a typo in the env var must NOT open the endpoint."""
    monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", value)
    if value.strip().lower() == "off":
        pytest.skip("that value is the documented off switch")
    assert mcp_auth.auth_disabled() is False
    app = mcp_unified.build_http_app(_dummy_fastmcp(), token_file=tmp_path / "mcp_token")
    with TestClient(app, base_url=BASE_URL) as client:
        assert _post(client).status_code == 401


def test_build_http_app_wraps_when_auth_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv("CONTEXTPULSE_MCP_AUTH", raising=False)
    target = tmp_path / "mcp_token"
    app = mcp_unified.build_http_app(_dummy_fastmcp(), token_file=target)
    assert isinstance(app, mcp_auth.BearerAuthASGI)
    token = target.read_text(encoding="utf-8").strip()
    with TestClient(app, base_url=BASE_URL) as client:
        assert _post(client).status_code == 401
        assert _post(client, {"Authorization": f"Bearer {token}"}).status_code == 200


# ── 9: setup.py writes an http+headers entry and preserves neighbours ─

def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_setup_claude_code_merges_and_is_idempotent(tmp_path):
    from contextpulse_sight import setup

    cfg = tmp_path / "claude.json"
    cfg.write_text(
        json.dumps({
            "numFolders": 3,
            "mcpServers": {
                "other-server": {"command": "some-other-mcp", "args": ["--x"]},
            },
        }),
        encoding="utf-8",
    )

    assert setup.setup_client("claude-code", token="tok-abc", paths=[cfg]) is True
    data = _read(cfg)

    assert data["mcpServers"]["contextpulse"] == {
        "type": "http",
        "url": "http://127.0.0.1:8420/mcp",
        "headers": {"Authorization": "Bearer tok-abc"},
    }
    assert data["mcpServers"]["other-server"] == {
        "command": "some-other-mcp", "args": ["--x"],
    }
    assert data["numFolders"] == 3, "unrelated top-level keys must survive"

    assert setup.setup_client("claude-code", token="tok-abc", paths=[cfg]) is True
    assert _read(cfg) == data, "second run changed the file"


def test_setup_replaces_a_stale_stdio_entry(tmp_path):
    """The old builds wrote a stdio `contextpulse-sight` entry; it must go."""
    from contextpulse_sight import setup

    cfg = tmp_path / "claude.json"
    cfg.write_text(
        json.dumps({"mcpServers": {
            "contextpulse-sight": {"command": "contextpulse-sight-mcp", "args": []},
            "contextpulse": {"type": "http", "url": "http://127.0.0.1:8420/mcp"},
        }}),
        encoding="utf-8",
    )
    setup.setup_client("claude-code", token="tok-abc", paths=[cfg])
    servers = _read(cfg)["mcpServers"]

    assert "contextpulse-sight" not in servers
    assert servers["contextpulse"]["headers"]["Authorization"] == "Bearer tok-abc"


def test_setup_creates_a_missing_config_file(tmp_path):
    from contextpulse_sight import setup

    cfg = tmp_path / "nested" / "claude.json"
    assert setup.setup_client("claude-code", token="tok-abc", paths=[cfg]) is True
    assert _read(cfg)["mcpServers"]["contextpulse"]["type"] == "http"


def test_setup_leaves_a_corrupt_config_alone(tmp_path):
    """Never silently truncate a user's config we could not parse."""
    from contextpulse_sight import setup

    cfg = tmp_path / "claude.json"
    cfg.write_text("{not json at all", encoding="utf-8")
    assert setup.setup_client("claude-code", token="tok-abc", paths=[cfg]) is False
    assert cfg.read_text(encoding="utf-8") == "{not json at all"


# ── config snippets ──────────────────────────────────────────────────

@pytest.mark.parametrize("client", ["claude-code", "cursor", "gemini", "claude-desktop"])
def test_config_snippet_is_valid_json_carrying_the_token(client):
    text = mcp_auth.config_snippet(client, token="tok-abc", port=PORT)
    body = json.loads(text[text.index("{"):])
    assert "tok-abc" in text
    assert body["mcpServers"]["contextpulse"]


def test_config_snippet_rejects_an_unknown_client():
    with pytest.raises(ValueError, match="unknown client"):
        mcp_auth.config_snippet("emacs", token="tok-abc")


def test_claude_desktop_header_arg_has_no_space_after_the_colon():
    """mcp-remote mangles `--header "K: V"` on Windows; `K:V` is the documented form."""
    entry = mcp_auth.server_entry("claude-desktop", token="tok-abc", port=PORT)
    header_arg = entry["args"][entry["args"].index("--header") + 1]
    assert header_arg == "Authorization:${AUTH_HEADER}"
    assert entry["env"]["AUTH_HEADER"] == "Bearer tok-abc"


# ── 10: static guard on mcp_unified ──────────────────────────────────

def test_mcp_unified_does_not_call_run_streamable_http():
    src = Path(mcp_unified.__file__).read_text(encoding="utf-8")
    assert 'run(transport="streamable-http")' not in src, (
        "mcp_unified must serve the wrapped ASGI app via uvicorn, not FastMCP.run() "
        "-- FastMCP.run() has no seam to insert BearerAuthASGI into."
    )
    assert "transport_security=" in src, (
        "TransportSecuritySettings must be passed explicitly, not left to FastMCP's "
        "silent localhost auto-default."
    )


def test_stdio_transport_is_still_unauthenticated_by_design():
    """stdio is spawned by the client as the same user; a token buys nothing."""
    src = Path(mcp_unified.__file__).read_text(encoding="utf-8")
    assert 'run(transport="stdio")' in src


def test_transport_security_pins_the_port_not_a_wildcard():
    settings = mcp_unified.build_transport_security(8420)
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["127.0.0.1:8420", "localhost:8420", "[::1]:8420"]
    assert settings.allowed_origins == [
        "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
    ]
