# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for local bearer-token auth on the unified MCP HTTP endpoint.

Written before contextpulse_core.mcp_auth existed, so the wrapper was built
against these tests rather than the other way round.

The fixture deliberately wraps a REAL FastMCP streamable-http app -- not a
stand-in -- and reuses the production TransportSecuritySettings builder from
mcp_unified rather than a hand-copied dict, so a change to the production
settings shows up here instead of drifting silently.
"""

import asyncio
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
def auth_client(tmp_path):
    """TestClient over BearerAuthASGI(FastMCP.streamable_http_app()).

    token_file points at a path that does not exist, on purpose: the wrapper
    re-reads its token when that file changes, and defaulting to the real
    %APPDATA% token file would both read David's live credential and make
    these tests depend on whether he has one.
    """
    app = mcp_auth.BearerAuthASGI(
        _dummy_fastmcp().streamable_http_app(), TOKEN, token_file=tmp_path / "absent"
    )
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


# ── the SSE shape production actually serves ─────────────────────────

def test_gate_holds_on_the_sse_response_shape(tmp_path):
    """The fixture above sets json_response=True; production does not.

    Same gate, the response body framing differs. Without this, every
    assertion in this file describes a server configuration that is not the
    one shipped.
    """
    app = FastMCP(
        "t", host="127.0.0.1", port=PORT, stateless_http=True,
        transport_security=mcp_unified.build_transport_security(PORT),
    )

    @app.tool()
    def dummy_probe() -> str:
        """Present so tools/list is non-empty."""
        return "ok"

    wrapped = mcp_auth.BearerAuthASGI(
        app.streamable_http_app(), TOKEN, token_file=tmp_path / "absent"
    )
    with TestClient(wrapped, base_url=BASE_URL) as client:
        assert _post(client).status_code == 401
        ok = _post(client, {"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
        assert ok.headers["content-type"].startswith("text/event-stream")
        assert "dummy_probe" in ok.text


# ── non-http scopes ──────────────────────────────────────────────────

def test_lifespan_scope_passes_through(tmp_path):
    """Eating lifespan would leave the session manager unstarted."""
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    wrapper = mcp_auth.BearerAuthASGI(inner, TOKEN, token_file=tmp_path / "absent")
    asyncio.run(wrapper({"type": "lifespan"}, _noop_receive, _noop_send))
    assert seen == ["lifespan"]


def test_websocket_scope_is_refused_not_forwarded(tmp_path):
    """A future websocket route must not inherit an ungated path."""
    seen = []
    sent = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    async def send(message):
        sent.append(message)

    wrapper = mcp_auth.BearerAuthASGI(inner, TOKEN, token_file=tmp_path / "absent")
    asyncio.run(wrapper({"type": "websocket"}, _noop_receive, send))
    assert seen == [], "websocket scope reached the app without authentication"
    assert sent == [{"type": "websocket.close", "code": 1008}]


# ── live rotation ────────────────────────────────────────────────────

class TestRegenerateTakesEffectWithoutARestart:
    """B1-4. The server captured its token once, at startup. Regenerating
    left the OLD token working and the NEW one dead -- the opposite of what
    someone clicking Regenerate because they think a token leaked is asking
    for.
    """

    def _client(self, tmp_path):
        token_file = tmp_path / "mcp_token"
        token = mcp_auth.load_or_create_token(token_file)
        app = mcp_auth.BearerAuthASGI(
            _dummy_fastmcp().streamable_http_app(), token, token_file=token_file
        )
        return token_file, token, TestClient(app, base_url=BASE_URL)

    def test_the_old_token_stops_working_and_the_new_one_starts(self, tmp_path):
        token_file, old, client = self._client(tmp_path)
        with client:
            assert _post(client, {"Authorization": f"Bearer {old}"}).status_code == 200

            new = mcp_auth.regenerate_token(token_file)
            assert new != old

            assert _post(client, {"Authorization": f"Bearer {old}"}).status_code == 401, (
                "the revoked token still works"
            )
            assert _post(client, {"Authorization": f"Bearer {new}"}).status_code == 200

    def test_an_unchanged_file_is_not_re_read(self, tmp_path, monkeypatch):
        """One stat per request, not one read -- the file is only opened when
        its stamp moved."""
        token_file, token, client = self._client(tmp_path)
        reads: list[str] = []
        real_read = Path.read_text

        def spy(self, *a, **kw):
            reads.append(str(self))
            return real_read(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy)
        with client:
            for _ in range(3):
                assert _post(client, {"Authorization": f"Bearer {token}"}).status_code == 200
        assert str(token_file) not in reads

    def test_a_deleted_token_file_keeps_the_running_token(self, tmp_path):
        """Rotation unlinks before it creates; a request in that window must
        neither open the endpoint nor lock out a working client."""
        token_file, token, client = self._client(tmp_path)
        with client:
            token_file.unlink()
            assert _post(client, {"Authorization": f"Bearer {token}"}).status_code == 200
            assert _post(client).status_code == 401

    def test_the_stamp_carries_the_file_index(self, tmp_path):
        """The stamp's own contents, so the behavioural test below cannot pass
        for the wrong reason on a filesystem that happens not to tunnel."""
        token_file, _token, client = self._client(tmp_path)
        gate = client.app
        st = os.stat(token_file)
        assert st.st_ino, "this filesystem reports no file index; the stamp needs one"
        assert st.st_ino in gate._file_stamp(), (
            f"st_ino is not in the stamp: {gate._file_stamp()}"
        )

    def test_a_recreated_file_with_an_identical_stamp_is_still_re_read(self, tmp_path):
        """`ctime` was put in the stamp to cover delete-and-recreate. On NTFS,
        file-system tunneling restores the creation time of a name deleted and
        recreated within ~15 s, and a replacement token is always the same
        length -- so on exactly the path it was widened for, the triple
        collapsed to `mtime_ns` alone. `st_ino` (the NTFS file index) does
        change, so it is what actually carries the case.

        mtime is forced back with os.utime, which removes the accident that
        made the old stamp look adequate: a regenerate landing hundreds of
        milliseconds after the original write.
        """
        token_file, old, client = self._client(tmp_path)
        before = os.stat(token_file)

        new = mcp_auth.TOKEN_PREFIX + "b" * (len(old) - len(mcp_auth.TOKEN_PREFIX))
        assert len(new) == len(old) and new != old

        token_file.unlink()
        token_file.write_text(new, encoding="utf-8")
        os.utime(token_file, ns=(before.st_atime_ns, before.st_mtime_ns))

        after = os.stat(token_file)
        assert after.st_size == before.st_size, "the two tokens must be the same length"
        assert after.st_mtime_ns == before.st_mtime_ns, "os.utime did not take"

        with client:
            assert _post(client, {"Authorization": f"Bearer {old}"}).status_code == 401, (
                "a token replaced under an unchanged mtime/size is still served"
            )
            assert _post(client, {"Authorization": f"Bearer {new}"}).status_code == 200

    def test_an_empty_token_file_keeps_the_running_token(self, tmp_path):
        """Seen mid-write: zero bytes must never mean 'no auth'."""
        token_file, token, client = self._client(tmp_path)
        with client:
            token_file.write_text("", encoding="utf-8")
            assert _post(client).status_code == 401
            assert _post(client, {"Authorization": "Bearer "}).status_code == 401
            assert _post(client, {"Authorization": f"Bearer {token}"}).status_code == 200


async def _noop_receive():
    return {"type": "lifespan.startup"}


async def _noop_send(message):
    return None


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


def test_empty_token_file_is_replaced_not_refused(tmp_path, caplog):
    """B1-5. Raising here bricked the endpoint: uvicorn never started and the
    watchdog relaunched into the same crash forever. An empty file cannot be a
    live credential, so regenerating is strictly safer than refusing."""
    target = tmp_path / "mcp_token"
    target.write_text("", encoding="utf-8")
    caplog.set_level("WARNING")

    token = mcp_auth.load_or_create_token(target)

    assert token
    assert target.read_text(encoding="utf-8").strip() == token
    assert any("holds no token" in r.message for r in caplog.records)


def test_whitespace_only_token_file_is_replaced(tmp_path):
    target = tmp_path / "mcp_token"
    target.write_text("   \n\t\n", encoding="utf-8")
    assert mcp_auth.load_or_create_token(target).strip()


def test_a_replaced_empty_token_file_is_still_permission_restricted(tmp_path):
    """The regeneration path must not skip the ACL the create path applies."""
    target = tmp_path / "mcp_token"
    target.write_text("", encoding="utf-8")
    mcp_auth.load_or_create_token(target)
    if sys.platform == "win32":
        out = subprocess.run(
            ["icacls", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout
        assert len(mcp_auth.parse_icacls_aces(out, target)) == 1
    else:
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_a_server_survives_an_emptied_token_file(tmp_path):
    """The end that matters: build_http_app does not raise, so uvicorn starts."""
    target = tmp_path / "mcp_token"
    target.write_text("", encoding="utf-8")
    app = mcp_unified.build_http_app(_dummy_fastmcp(), token_file=target)
    token = target.read_text(encoding="utf-8").strip()
    with TestClient(app, base_url=BASE_URL) as client:
        assert _post(client).status_code == 401
        assert _post(client, {"Authorization": f"Bearer {token}"}).status_code == 200


# ── token shape ──────────────────────────────────────────────────────

def test_a_new_token_carries_the_redactable_prefix(tmp_path):
    """B1-6. The Settings dialog renders the bare token with no "Bearer " in
    front of it, so it needs a shape ContextPulse's own OCR redaction knows."""
    token = mcp_auth.load_or_create_token(tmp_path / "mcp_token")
    assert token.startswith(mcp_auth.TOKEN_PREFIX)
    assert len(token) >= len(mcp_auth.TOKEN_PREFIX) + 43


def test_the_prefix_is_not_required_to_verify(tmp_path):
    """Tokens issued before the prefix existed must keep working."""
    legacy = "a" * 43
    target = tmp_path / "mcp_token"
    target.write_text(legacy, encoding="utf-8")
    assert mcp_auth.load_or_create_token(target) == legacy
    assert mcp_auth.verify(legacy, legacy) is True


def test_a_new_token_is_redacted_by_the_capture_pipeline(tmp_path):
    """The claim the prefix exists to make, against a REAL generated token.

    packages/core/tests/test_redact.py pins the pattern against a synthetic
    value. This pins the two ends together: whatever load_or_create_token
    actually produces must be something the shipped redactor removes. A
    pattern and a generator can each be correct and still not meet.
    """
    from contextpulse_core.redact import redact_sensitive

    token = mcp_auth.load_or_create_token(tmp_path / "mcp_token")
    screen_text = f"MCP Access\n{token}\nToken file: C:\\Users\\x\\mcp_token"
    cleaned = redact_sensitive(screen_text)

    assert token not in cleaned
    assert "[REDACTED:CP_MCP_TOKEN]" in cleaned
    assert "MCP Access" in cleaned, "redaction ate the surrounding context"


def test_the_token_is_forced_to_disk_before_it_is_handed_out(tmp_path, monkeypatch):
    """R2-2, token half. write_text() closing the handle hands the bytes to the
    OS cache, not to the platter. load_or_create_token returns the token to a
    caller that immediately prints it into a client config; a power cut before
    the cache flushed leaves an EMPTY token file behind a configured client --
    the B1-5 shape, arrived at from the other direction.
    """
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync_spy(fd):
        result = real_fsync(fd)
        synced.append(os.fstat(fd).st_size)
        return result

    monkeypatch.setattr(mcp_auth.os, "fsync", fsync_spy)
    token = mcp_auth.load_or_create_token(tmp_path / "mcp_token")

    assert len(token.encode("utf-8")) in synced, (
        f"the token file was never fsynced: {synced}"
    )


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


class TestTheOffSwitchIsNotReachableFromADotEnvFile:
    """B1-3. config.py runs load_dotenv(override=True) at import, so a .env in
    the working directory beats the real environment. The off switch was made
    env-only so it could not become a persisted setting; a .env in a checkout
    is a persisted setting.
    """

    def _write_dotenv(self, tmp_path, monkeypatch, value="off"):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            f"CONTEXTPULSE_MCP_AUTH={value}\n", encoding="utf-8"
        )

    def test_a_dotenv_off_is_refused_and_says_so(self, tmp_path, monkeypatch, caplog):
        self._write_dotenv(tmp_path, monkeypatch)
        # Reproduce what load_dotenv(override=True) does to os.environ.
        monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", "off")
        monkeypatch.setattr(mcp_auth.env_guard, "SNAPSHOT_IS_PRE_DOTENV", True)
        monkeypatch.setattr(mcp_auth.env_guard, "PROCESS_ENV", {})  # not in the real env
        caplog.set_level("WARNING")

        assert mcp_auth.auth_disabled() is False
        assert any(".env" in r.message for r in caplog.records)

    def test_a_dotenv_off_does_not_open_the_endpoint(self, tmp_path, monkeypatch):
        """The end the user actually sees: still 401."""
        self._write_dotenv(tmp_path, monkeypatch)
        monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", "off")
        monkeypatch.setattr(mcp_auth.env_guard, "SNAPSHOT_IS_PRE_DOTENV", True)
        monkeypatch.setattr(mcp_auth.env_guard, "PROCESS_ENV", {})

        app = mcp_unified.build_http_app(
            _dummy_fastmcp(), token_file=tmp_path / "mcp_token"
        )
        with TestClient(app, base_url=BASE_URL) as client:
            assert _post(client).status_code == 401

    def test_the_real_environment_still_wins_when_both_are_set(self, tmp_path, monkeypatch):
        self._write_dotenv(tmp_path, monkeypatch)
        monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", "off")
        monkeypatch.setattr(mcp_auth.env_guard, "SNAPSHOT_IS_PRE_DOTENV", True)
        monkeypatch.setattr(
            mcp_auth.env_guard, "PROCESS_ENV", {"CONTEXTPULSE_MCP_AUTH": "off"}
        )
        assert mcp_auth.auth_disabled() is True

    def test_a_dotenv_that_does_not_set_it_changes_nothing(self, tmp_path, monkeypatch):
        self._write_dotenv(tmp_path, monkeypatch, value="on")
        monkeypatch.setenv("CONTEXTPULSE_MCP_AUTH", "off")
        monkeypatch.setattr(mcp_auth.env_guard, "SNAPSHOT_IS_PRE_DOTENV", True)
        monkeypatch.setattr(mcp_auth.env_guard, "PROCESS_ENV", {})
        assert mcp_auth.auth_disabled() is True

    def test_the_cli_flag_is_unaffected_by_any_of_this(self, tmp_path, monkeypatch):
        """--no-auth is a real argument on a real command line; it always wins."""
        self._write_dotenv(tmp_path, monkeypatch)
        app = mcp_unified.build_http_app(
            _dummy_fastmcp(), no_auth=True, token_file=tmp_path / "mcp_token"
        )
        with TestClient(app, base_url=BASE_URL) as client:
            assert _post(client).status_code == 200

    def test_env_guard_reports_its_own_trustworthiness(self):
        """A polluted snapshot must read as unknown, not as a confident value."""
        from contextpulse_core import env_guard

        assert isinstance(env_guard.SNAPSHOT_IS_PRE_DOTENV, bool)
        assert env_guard.process_env("DEFINITELY_NOT_SET_ANYWHERE") == ""


class TestTheDotEnvGuardReadsTheFilesConfigActuallyLoaded:
    """R2-1. The guard and config.py used to search different places.

    `config.py` calls `load_dotenv()` with no path, i.e. `find_dotenv(usecwd=False)`:
    a walk up from **config.py's own directory**. The guard called
    `find_dotenv(usecwd=True)`: a walk up from the **cwd**. With a `.env` in each
    tree they resolve different files, so the guard could clear a file that set
    nothing while never reading the one that set the off switch.

    These tests deliberately do NOT let `monkeypatch.chdir` stand in for
    config.py's resolution -- the two locations are kept distinct and the guard
    is asked about each in turn.
    """

    @staticmethod
    def _dotenv(path: Path, value: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{mcp_auth.AUTH_ENV_VAR}={value}\n", encoding="utf-8")
        return path

    @staticmethod
    def _as_if_dotenv_had_won(monkeypatch):
        """os.environ as load_dotenv(override=True) leaves it: value present,
        real process environment clean, snapshot trustworthy."""
        monkeypatch.setenv(mcp_auth.AUTH_ENV_VAR, "off")
        monkeypatch.setattr(mcp_auth.env_guard, "SNAPSHOT_IS_PRE_DOTENV", True)
        monkeypatch.setattr(mcp_auth.env_guard, "PROCESS_ENV", {})

    def _two_trees(self, tmp_path, monkeypatch, loaded_value, cwd_value):
        loaded = self._dotenv(tmp_path / "module_tree" / ".env", loaded_value)
        cwd = tmp_path / "cwd_tree"
        self._dotenv(cwd / ".env", cwd_value)
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(mcp_auth.config, "LOADED_DOTENV_PATHS", [str(loaded)])
        self._as_if_dotenv_had_won(monkeypatch)

    def test_off_in_the_file_config_loaded_keeps_auth_on(self, tmp_path, monkeypatch, caplog):
        """The drift case. Only the file config.py loaded says off; the cwd's
        .env is innocent. A cwd-only search sees nothing and opens the endpoint."""
        self._two_trees(tmp_path, monkeypatch, loaded_value="off", cwd_value="on")
        caplog.set_level("WARNING")

        assert mcp_auth._dotenv_sets_off() is True
        assert mcp_auth.auth_disabled() is False
        assert any(".env" in r.message for r in caplog.records)

    def test_off_in_a_cwd_dotenv_keeps_auth_on(self, tmp_path, monkeypatch):
        """The mirror case. The cwd stays an EXTRA candidate rather than being
        dropped: a hit here only ever leaves auth ON, so a false positive costs
        a warning while a false negative opens the endpoint."""
        self._two_trees(tmp_path, monkeypatch, loaded_value="on", cwd_value="off")

        assert mcp_auth._dotenv_sets_off() is True
        assert mcp_auth.auth_disabled() is False

    def test_neither_file_sets_it_so_the_environment_is_believed(self, tmp_path, monkeypatch):
        self._two_trees(tmp_path, monkeypatch, loaded_value="on", cwd_value="on")

        assert mcp_auth._dotenv_sets_off() is False
        assert mcp_auth.auth_disabled() is True

    def test_the_workspace_dotenv_env_var_is_still_consulted(self, tmp_path, monkeypatch):
        """CONTEXTPULSE_DOTENV can be set after config.py imported, so it is read
        live as well as through LOADED_DOTENV_PATHS."""
        workspace = self._dotenv(tmp_path / "workspace" / ".env", "off")
        self._two_trees(tmp_path, monkeypatch, loaded_value="on", cwd_value="on")
        monkeypatch.setenv("CONTEXTPULSE_DOTENV", str(workspace))

        assert mcp_auth._dotenv_sets_off() is True
        assert mcp_auth.auth_disabled() is False


_CONFIG_PROBE = """\
import json, os
from contextpulse_core import config

print(json.dumps({
    "paths": [os.path.abspath(p) for p in config.LOADED_DOTENV_PATHS],
    "value": os.environ.get("CONTEXTPULSE_MCP_AUTH", ""),
}))
"""


def test_config_publishes_the_dotenv_it_loaded_and_never_the_cwds(tmp_path):
    """R2-1, the config half: LOADED_DOTENV_PATHS must be what load_dotenv()
    really resolved. Out of process, because config.py resolves once at import.

    The probe runs with a `.env` sitting in its cwd that sets the off switch.
    config.py's no-argument search is module-relative, so that file must not
    appear in the published list and must not reach os.environ -- which is
    exactly why a cwd-only guard was looking in the wrong place.
    """
    (tmp_path / ".env").write_text("CONTEXTPULSE_MCP_AUTH=off\n", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(_CONFIG_PROBE, encoding="utf-8")

    env = dict(os.environ)
    env.pop("CONTEXTPULSE_MCP_AUTH", None)
    env.pop("CONTEXTPULSE_DOTENV", None)
    result = subprocess.run(
        [sys.executable, str(probe)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    published = json.loads(result.stdout.strip().splitlines()[-1])

    assert published["value"] != "off", "config.py must not read the cwd's .env"
    cwd_marker = os.path.normcase(str(tmp_path))
    assert not [
        p for p in published["paths"] if os.path.normcase(p).startswith(cwd_marker)
    ], f"LOADED_DOTENV_PATHS leaked a cwd-relative file: {published['paths']}"


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
