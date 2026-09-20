# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Local bearer-token auth for the unified MCP HTTP endpoint.

The endpoint at 127.0.0.1:8420/mcp serves every ContextPulse tool -- screen
OCR, clipboard history, keystroke bursts, transcriptions, memory recall. Until
this module existed the loopback bind was the only control, which means every
process running as any local user could read all of it.

What this defends against: any local caller that is not holding the token --
other Windows accounts, sandboxed apps with loopback rights, other agents and
MCP clients, malware. What it does NOT defend against: a process running as the
user who owns the token file, which can simply read the file (and can read the
SQLite databases directly anyway). That boundary is deliberate.

The Host/Origin half of the defence lives in the MCP library
(mcp.server.transport_security) and is wired up explicitly in mcp_unified.

Design notes worth keeping:
  - The wrapper is pure ASGI and sits OUTSIDE the Starlette app, so a missing
    or wrong token is 401 before any MCP machinery runs. Ordering consequence:
    bad token + bad Origin is 401, not 403.
  - The library's native auth (AuthSettings + token_verifier) is OAuth-shaped
    and requires an issuer_url; handing clients a fake issuer to discover on a
    401 buys nothing over twenty lines of ASGI.
"""

import hmac
import json
import logging
import os
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path

# isort: off
# env_guard FIRST, and before config: it snapshots the real process
# environment, and importing config runs load_dotenv(override=True), after
# which a .env file has already overwritten os.environ. See auth_disabled().
from contextpulse_core import env_guard
from contextpulse_core import config
from contextpulse_core.config import APPDATA_DIR

# isort: on

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "mcp_token"
TOKEN_FILE = APPDATA_DIR / TOKEN_FILENAME

# Tokens carry a fixed prefix so ContextPulse's own OCR redaction can
# recognise one on sight. Without it a bare token_urlsafe string matches no
# pattern in contextpulse_sight.redact, so a screenshot taken while the
# Settings dialog is showing the token stored the token itself, unredacted, in
# activity.db -- readable back out through get_screen_text and search_history.
# The clipboard copy was always safe because it is embedded in a snippet that
# says "Bearer "; the on-screen value was not.
# Nothing VERIFIES the prefix: tokens issued before this existed stay valid,
# and verify() is a plain constant-time compare of whatever is on disk.
TOKEN_PREFIX = "cpmcp_"

AUTH_ENV_VAR = "CONTEXTPULSE_MCP_AUTH"
AUTH_DISABLED_PREFIX = "MCP AUTH DISABLED"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420
SERVER_NAME = "contextpulse"

_WWW_AUTH_ERROR = "invalid_token"
_WWW_AUTH_DESC = "Authentication required"

# How long a losing racer waits for the winner to finish writing the token.
_READ_RETRIES = 50
_READ_RETRY_SLEEP = 0.02


# ── the off switch ───────────────────────────────────────────────────

def _dotenv_sets_off() -> bool:
    """Would a .env file, on its own, have set the off switch?

    Reads config.LOADED_DOTENV_PATHS -- the files config.py really fed to
    load_dotenv -- instead of repeating the search here. Repeating it was the
    bug: config.py calls load_dotenv() with no path, which is
    find_dotenv(usecwd=False), a walk up from config.py's OWN directory, while
    this function called find_dotenv(usecwd=True), a walk up from the cwd. With
    a .env in each tree the two layers resolve different files, so the guard
    could clear a file that set nothing while never opening the one that set
    the switch.

    Two further candidates are consulted on top of that list, not instead of
    it: CONTEXTPULSE_DOTENV as it stands NOW (it can be set after config.py
    imported), and the cwd's .env (a caller may have loaded it itself, and a
    future import order may run this before config.py). Every extra candidate
    can only make the answer True, and True leaves auth ON -- so a false
    positive costs a warning and a working endpoint, while a false negative
    opens the endpoint. That asymmetry is why the union is the safe shape.
    """
    try:
        from dotenv import dotenv_values, find_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a hard dependency
        return False

    candidates: list[str] = list(config.LOADED_DOTENV_PATHS)
    candidates.append(os.environ.get("CONTEXTPULSE_DOTENV", ""))
    candidates.append(find_dotenv(usecwd=True))

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        try:
            values = dotenv_values(candidate)
        except OSError:
            continue
        if (values.get(AUTH_ENV_VAR) or "").strip().lower() == "off":
            return True
    return False


def auth_disabled() -> bool:
    """True only for the exact documented off switch, set in the real environment.

    Fails closed twice over.

    First on the value: a typo must not open the endpoint, so "0", "false"
    and "no" all leave auth ON. Only the literal "off" counts.

    Then on the SOURCE. config.py runs load_dotenv(override=True) at import,
    so a `.env` sitting in whatever directory the server was started from
    beats the real environment -- which would turn a security switch that was
    deliberately made env-only into a persisted file setting, the exact thing
    the spec ruled out. Only the process environment (or --no-auth on the
    command line) may disable auth. env_guard's pre-dotenv snapshot settles
    the case where both are set; failing that, a value a .env could account
    for is refused, with a warning naming why.
    """
    if os.environ.get(AUTH_ENV_VAR, "").strip().lower() != "off":
        return False

    if env_guard.process_env(AUTH_ENV_VAR).strip().lower() == "off":
        return True

    if _dotenv_sets_off():
        logger.warning(
            "Ignoring %s=off: it comes from a .env file, not the environment. "
            "MCP auth stays ON. Set it in the real environment, or pass "
            "--no-auth, if you meant it.",
            AUTH_ENV_VAR,
        )
        return False

    return True


def disabled_banner(reason: str) -> str:
    """The one-line WARNING logged at startup when auth is off."""
    return (
        f"{AUTH_DISABLED_PREFIX} ({reason}) -- "
        "every local process can call every tool"
    )


# ── file permissions ─────────────────────────────────────────────────

def parse_icacls_aces(stdout: str, path: Path | str) -> list[str]:
    """Extract the ACE lines from `icacls <path>` output.

    Selects on the `principal:(perms)` shape rather than on line position or
    the trailing "Successfully processed" summary, which is localised. A
    Windows path prefix ("C:\\...") contains ":\\", never ":(", so the first
    line's path is stripped rather than matched.
    """
    prefix = str(path)
    aces: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if ":(" not in line:
            continue
        if line.lower().startswith(prefix.lower()):
            line = line[len(prefix):].strip()
        if line:
            aces.append(line)
    return aces


def _restrict_posix(path: Path) -> bool:
    os.chmod(path, 0o600)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != 0o600:
        logger.error("Token file %s is mode %o, expected 600", path, mode)
        return False
    return True


def _restrict_windows(path: Path) -> bool:
    user = os.environ.get("USERNAME", "")
    if not user:
        logger.error("USERNAME is unset; cannot restrict ACL on %s", path)
        return False
    domain = os.environ.get("USERDOMAIN", "")
    principal = f"{domain}\\{user}" if domain else user

    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
            check=True, **kwargs,
        )
        shown = subprocess.run(["icacls", str(path)], check=True, **kwargs).stdout
    except (subprocess.CalledProcessError, OSError):
        logger.exception("icacls failed on %s; token file permissions are NOT restricted", path)
        return False

    aces = parse_icacls_aces(shown, path)
    forbidden = ("(I)", "builtin\\users", "everyone", "authenticated users")
    ok = (
        len(aces) == 1
        and user.lower() in aces[0].lower()
        and not any(bad in aces[0].lower() for bad in forbidden)
    )
    if not ok:
        logger.error("Token file %s has unexpected ACL: %s", path, aces)
    return ok


def restrict_to_user(path: Path) -> bool:
    """Make `path` readable only by the current user. True if verified.

    A False return is logged as ERROR and is NOT fatal: the token still
    defeats every caller that is not this user, and a caller that IS this
    user can read the databases directly regardless.
    """
    try:
        if sys.platform == "win32":
            return _restrict_windows(path)
        return _restrict_posix(path)
    except OSError:
        logger.exception("Could not restrict permissions on %s", path)
        return False


# ── token lifecycle ──────────────────────────────────────────────────

def _read_existing(path: Path) -> str | None:
    """Read a token that another process may still be writing.

    Returns None when there is no usable token: the file is absent, or it
    stayed empty for the whole retry window. The retries matter -- a second
    process claims the name before it writes the secret, and a loser that gave
    up instantly would delete the winner's file.

    An empty token is never returned as a token. verify() would then be
    comparing every caller against "", and while verify() refuses an empty
    expected value, encoding that safety in two places invites one of them to
    change.
    """
    for _ in range(_READ_RETRIES):
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("Could not read token file %s", path)
            raise
        if token:
            return token
        time.sleep(_READ_RETRY_SLEEP)
    return None


def load_or_create_token(token_file: Path | str | None = None, _attempt: int = 0) -> str:
    """Return this install's MCP bearer token, creating it on first use.

    Race-safe against a second process (the daemon watchdog and a hand-started
    MCP server can both reach this within milliseconds of each other): the name
    is claimed with O_CREAT|O_EXCL on the FINAL path, which is the only
    primitive that fails rather than clobbers on both Windows and POSIX. The
    loser of that race re-reads. os.replace() from a temp file would NOT do --
    it overwrites, so two racers would end up serving different tokens.

    Permissions are applied to the empty claimed file BEFORE the secret bytes
    are written, so the token never exists on disk under a permissive ACL.

    A file that exists but holds no usable token is replaced, not refused.
    Raising there bricked the endpoint permanently: uvicorn never started, the
    watchdog relaunched into the same crash every loop, and nothing recovered
    until a human deleted the file. An empty file provably cannot be a live
    credential -- no client can be holding it -- so regenerating is strictly
    safer than refusing to start. It is logged at WARNING because a token file
    that emptied itself means a client needs reconfiguring, and this repo has
    a documented history of a filename-pattern secret scanner zeroing files.
    """
    path = Path(token_file) if token_file is not None else TOKEN_FILE

    existing = _read_existing(path)
    if existing:
        return existing

    if path.exists():
        logger.warning(
            "MCP token file %s exists but holds no token -- generating a new one. "
            "Any client configured with the previous token must be re-run through "
            "`contextpulse --setup`.",
            path,
        )
        path.unlink(missing_ok=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Someone else claimed the name between our read and our create.
        lost = _read_existing(path)
        if lost:
            return lost
        if _attempt >= 1:
            raise RuntimeError(
                f"MCP token file {path} keeps coming back empty. Delete it and "
                "restart the MCP server."
            ) from None
        return load_or_create_token(path, _attempt=_attempt + 1)

    os.close(fd)
    restrict_to_user(path)
    try:
        path.write_text(token, encoding="utf-8")
    except OSError:
        # Leave no empty file behind -- it would poison every later read.
        path.unlink(missing_ok=True)
        raise
    logger.info("Created MCP access token at %s", path)
    return token


def regenerate_token(token_file: Path | str | None = None) -> str:
    """Delete the token file and create a new one. Returns the new token."""
    path = Path(token_file) if token_file is not None else TOKEN_FILE
    path.unlink(missing_ok=True)
    return load_or_create_token(path)


# ── verification ─────────────────────────────────────────────────────

def extract_bearer(header_value: str | None) -> str | None:
    """Pull the token out of an Authorization header, or None.

    The scheme is case-insensitive (RFC 7235); an empty token and any other
    scheme both return None.
    """
    if not header_value:
        return None
    scheme, _, rest = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None


def verify(presented: str | None, expected: str | None) -> bool:
    """Constant-time token comparison. Empty on either side is always False."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


class BearerAuthASGI:
    """Pure-ASGI bearer gate, wrapped OUTSIDE the MCP Starlette app.

    Only `lifespan` passes through ungated -- that scope is what starts the
    session manager, so eating it would hang the server, and it carries no
    request. Everything that is not `http` is refused rather than forwarded:
    the app has no websocket routes today, and "we forward what we do not
    understand" is how an unauthenticated path appears the day it gains one.
    """

    def __init__(self, app, token: str, token_file: Path | str | None = None) -> None:
        if not token:
            raise ValueError("BearerAuthASGI requires a non-empty token")
        self.app = app
        self._token = token
        self._token_file = Path(token_file) if token_file is not None else TOKEN_FILE
        self._stamp = self._file_stamp()

    def _file_stamp(self) -> tuple | None:
        """Cheap identity of the token file: one stat, no read."""
        try:
            st = os.stat(self._token_file)
        except OSError:
            return None
        # ctime as well as mtime: regeneration unlinks and recreates, and the
        # replacement is always the same length, so size alone proves nothing.
        return (st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    def current_token(self) -> str:
        """The live token, re-read when the file underneath has changed.

        Without this, "Regenerate token" in the Settings dialog left the OLD
        token working and the NEW one dead until someone restarted the MCP
        server -- the exact opposite of what a user clicking Regenerate
        because they think a token leaked is asking for.

        Every failure path keeps the token already in hand. A file that is
        missing, unreadable, or momentarily empty mid-rotation must not open
        the endpoint or lock out a working client.
        """
        stamp = self._file_stamp()
        if stamp is None or stamp == self._stamp:
            return self._token
        try:
            rotated = self._token_file.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("Could not re-read %s; keeping the token in memory", self._token_file)
            return self._token
        if not rotated:
            return self._token  # mid-write; the writer will bump the stamp again
        if rotated != self._token:
            logger.info("MCP access token changed on disk -- now serving the new one")
        self._token = rotated
        self._stamp = stamp
        return self._token

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            logger.warning("Refusing %s scope on the MCP endpoint", scope_type)
            if scope_type == "websocket":
                # Closing before accept fails the handshake (HTTP 403).
                await send({"type": "websocket.close", "code": 1008})
            return

        header = None
        for key, value in scope.get("headers", []):
            if key == b"authorization":  # ASGI guarantees lowercased names
                header = value.decode("latin-1")
                break

        if not verify(extract_bearer(header), self.current_token()):
            await self._send_401(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_401(send) -> None:
        # Same shape the library's own RequireAuthMiddleware emits, so clients
        # that special-case it keep working.
        body = json.dumps(
            {"error": _WWW_AUTH_ERROR, "error_description": _WWW_AUTH_DESC}
        ).encode()
        www_auth = f'Bearer error="{_WWW_AUTH_ERROR}", error_description="{_WWW_AUTH_DESC}"'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", www_auth.encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


# ── client config snippets ───────────────────────────────────────────

def endpoint_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}/mcp"


def server_entry(
    client: str,
    token: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> dict:
    """The single mcpServers entry for one client, with the token embedded.

    One builder for --setup, --print-config and the settings dialog, so the
    three cannot drift apart.
    """
    client = client.lower().strip()
    if token is None:
        token = load_or_create_token()
    url = endpoint_url(host, port)
    auth = f"Bearer {token}"

    if client == "claude-code":
        return {"type": "http", "url": url, "headers": {"Authorization": auth}}
    if client == "cursor":
        return {"url": url, "headers": {"Authorization": auth}}
    if client == "gemini":
        # Gemini CLI keys streamable HTTP as httpUrl; `url` there is SSE.
        return {"httpUrl": url, "headers": {"Authorization": auth}}
    if client == "claude-desktop":
        # Desktop's own config is stdio-only, so it reaches an HTTP server
        # through mcp-remote. No space after the colon: mcp-remote's README
        # documents a Windows arg-mangling bug with "Header: value".
        return {
            "command": "npx",
            "args": ["mcp-remote", url, "--header", "Authorization:${AUTH_HEADER}"],
            "env": {"AUTH_HEADER": auth},
        }
    raise ValueError(
        f"unknown client {client!r}; expected one of "
        "claude-code, cursor, gemini, claude-desktop"
    )


_SNIPPET_NOTES = {
    "claude-code": "# ~/.claude.json  (or: claude mcp add --transport http -s user ...)",
    "cursor": "# .cursor/mcp.json",
    "gemini": "# ~/.gemini/settings.json",
    "claude-desktop": "# claude_desktop_config.json  (needs Node: npx mcp-remote)",
}


def config_snippet(
    client: str,
    token: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> str:
    """The copy-pasteable config block for one client."""
    entry = server_entry(client, token=token, host=host, port=port)
    note = _SNIPPET_NOTES[client.lower().strip()]
    body = json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)
    return f"{note}\n{body}"
