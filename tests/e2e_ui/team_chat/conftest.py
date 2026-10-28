"""Fixtures for the team chat browser suite.

The shared ``live_server`` is single-user with no feature flags, and
``collaboration/_multi_user_server.py`` binds no runner. Team chat needs both:
a multi-user header-auth server with ``OMNIGENT_FEATURES=team_chat`` and a
mock-LLM runner owned by Anna, who starts agent threads.

Each browser context names its user with ``X-Forwarded-Email``. Chromium does
not put context ``extra_http_headers`` on the WebSocket handshake, so each
context instead uses a per-email loopback forwarder as its proxy. The page
still opens ``server.base_url``; the forwarder adds the header to every
request, including the updates socket's Upgrade request.

The seed helpers call the ``/v1/chat/*`` REST contract from the design. They
fail until those routes exist.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import socketserver
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from omnigent.runner.identity import token_bound_runner_id
from tests._helpers.compat import apply_runner_env, compat_runner_cwd, runner_executable
from tests._helpers.live_server import isolated_local_server, terminate_process
from tests.e2e_ui.conftest import (
    _HEALTH_POLL_INTERVAL_S,
    _REPO_ROOT,
    _TEST_AGENT_YAML,
    set_fallback_mock_llm,
)

TEAM_CHAT_FEATURE = "team_chat"
ANNA = "anna@team-chat.test"
JENS = "jens@team-chat.test"

_TESTIDS_TS = _REPO_ROOT / "web" / "src" / "chat" / "testids.ts"
_TESTID_LINE = re.compile(r'^\s*(\w+):\s*"([\w-]+)",?\s*$', re.MULTILINE)
# A cold server boot plus runner tunnel can exceed the shared 30 s on CI.
_BOOT_TIMEOUT_S = 180.0


def _load_chat_test_ids() -> dict[str, str]:
    """Read the ``data-testid`` constants the web app uses for team chat."""
    ids = dict(_TESTID_LINE.findall(_TESTIDS_TS.read_text()))
    if not ids:
        raise AssertionError(f"no test ids parsed from {_TESTIDS_TS}")
    return ids


CHAT_TEST_IDS = _load_chat_test_ids()


def identity_headers(email: str) -> dict[str, str]:
    """Headers that make the header-auth server treat a request as *email*."""
    return {"X-Forwarded-Email": email}


_HEAD_END = b"\r\n\r\n"
_MAX_HEAD_BYTES = 64 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Dropped from forwarded requests; the forwarder sets Connection and identity itself.
_DROPPED_REQUEST_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "x-forwarded-email"}
)
_FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def _read_head(sock: socket.socket, buffered: bytes = b"") -> tuple[bytes, bytes]:
    """Read one HTTP request head; return it without its blank line, plus extra bytes."""
    buffer = buffered
    while _HEAD_END not in buffer:
        if len(buffer) > _MAX_HEAD_BYTES:
            raise ConnectionError("request head too large")
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("client closed before a full request head")
        buffer += chunk
    head, _, rest = buffer.partition(_HEAD_END)
    return head, rest


def _rewrite_head(head: bytes, email: str) -> tuple[bytes, tuple[str, int] | None]:
    """Turn a proxied request head into an origin request that names *email*.

    Returns the new head (with its blank line) and the absolute-form target's
    address, or ``None`` for an origin-form target. Plain requests get
    ``Connection: close`` so each one carries its own injected header.
    """
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    method, target, version = request_line.split(" ", 2)
    address = None
    if target.startswith("http://"):
        parts = urlsplit(target)
        address = (parts.hostname or "", parts.port or 80)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    kept = [f"{method} {target} {version}"]
    upgrade = False
    for line in header_lines:
        name = line.partition(":")[0].strip().lower()
        if name in _DROPPED_REQUEST_HEADERS:
            continue
        upgrade = upgrade or name == "upgrade"
        kept.append(line)
    kept.append("Connection: Upgrade" if upgrade else "Connection: close")
    kept.extend(f"{name}: {value}" for name, value in identity_headers(email).items())
    return ("\r\n".join(kept)).encode("latin-1") + _HEAD_END, address


def _pump(source: socket.socket, sink: socket.socket) -> None:
    """Copy bytes from *source* to *sink* until EOF, then half-close *sink*."""
    with suppress(OSError):
        while chunk := source.recv(65536):
            sink.sendall(chunk)
    with suppress(OSError):
        sink.shutdown(socket.SHUT_WR)


class _IdentityForwarder(socketserver.ThreadingTCPServer):
    """A loopback HTTP proxy that forwards every request as one *email*.

    Handles absolute-form requests and ``CONNECT`` tunnels; Chromium sends a
    ``ws://`` upgrade through ``CONNECT``, so the tunneled head is rewritten
    too. Only loopback targets are forwarded.
    """

    daemon_threads = True
    block_on_close = False

    def __init__(self, email: str) -> None:
        super().__init__(("127.0.0.1", 0), _ForwardHandler)
        self.email = email
        self._open_sockets: set[socket.socket] = set()
        self._sockets_lock = threading.Lock()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def track(self, sock: socket.socket) -> None:
        with self._sockets_lock:
            self._open_sockets.add(sock)

    def untrack(self, sock: socket.socket) -> None:
        with self._sockets_lock:
            self._open_sockets.discard(sock)
        sock.close()

    def close_open_sockets(self) -> None:
        """Close live tunnels (e.g. open updates sockets) so pump threads end."""
        with self._sockets_lock:
            open_sockets, self._open_sockets = self._open_sockets, set()
        for sock in open_sockets:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()


class _ForwardHandler(socketserver.BaseRequestHandler):
    server: _IdentityForwarder

    def handle(self) -> None:
        client: socket.socket = self.request
        self.server.track(client)
        upstream: socket.socket | None = None
        try:
            head, rest = _read_head(client)
            method, target = head.split(b" ", 2)[:2]
            if method == b"CONNECT":
                host, _, port = target.decode("latin-1").rpartition(":")
                address: tuple[str, int] | None = (host.strip("[]"), int(port))
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                head, rest = _read_head(client, rest)
                rewritten, _ = _rewrite_head(head, self.server.email)
            else:
                rewritten, address = _rewrite_head(head, self.server.email)
            if address is None or address[0] not in _LOOPBACK_HOSTS:
                client.sendall(_FORBIDDEN)
                return
            upstream = socket.create_connection(address, timeout=10.0)
            upstream.settimeout(None)
            self.server.track(upstream)
            upstream.sendall(rewritten + rest)
            reply = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
            reply.start()
            _pump(client, upstream)
            reply.join()
        except (OSError, ValueError):
            return
        finally:
            if upstream is not None:
                self.server.untrack(upstream)
            self.server.untrack(client)


@contextmanager
def identity_forwarder(email: str) -> Iterator[str]:
    """Run a loopback proxy that adds *email*'s identity header; yield its URL."""
    forwarder = _IdentityForwarder(email)
    thread = threading.Thread(target=forwarder.serve_forever, daemon=True)
    thread.start()
    try:
        yield forwarder.url
    finally:
        forwarder.shutdown()
        forwarder.server_close()
        forwarder.close_open_sockets()
        thread.join(timeout=5.0)


@dataclass(frozen=True)
class TeamChatServer:
    """A running team chat server.

    :param base_url: Loopback base URL, e.g. ``"http://127.0.0.1:51234"``.
    :param runner_owner: Email of the user who owns the runner, or ``None``
        when the server was spawned without one.
    :param runner_id: The runner's id, or ``None`` without a runner.
    :param database_uri: The server's SQLite URI, for seeds with no REST route.
    """

    base_url: str
    runner_owner: str | None
    runner_id: str | None
    database_uri: str


def _runner_online(base_url: str, runner_id: str, owner: str) -> tuple[bool, str]:
    """Ask, as *owner*, whether the runner is online; return the result and a reason."""
    try:
        resp = httpx.get(
            f"{base_url}/v1/runners/{runner_id}/status",
            headers=identity_headers(owner),
            timeout=2.0,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if resp.status_code == 200 and resp.json().get("online") is True:
        return True, "online"
    return False, f"status HTTP {resp.status_code}: {resp.text[:200]}"


@contextmanager
def _owned_runner(
    base_url: str, *, owner: str, binding_token: str, mock_llm_url: str, tmp: Path
) -> Iterator[str]:
    """Run a mock-LLM runner whose tunnel identifies as *owner*.

    ``OMNIGENT_DATABRICKS_EXTRA_HEADERS`` puts *owner*'s identity header on the
    tunnel handshake, so the server records *owner* instead of the loopback
    ``local`` user. Runner status is owner-scoped: online as *owner* proves it.
    """
    runner_id = token_bound_runner_id(binding_token)
    env = {
        **os.environ,
        "PYTHONPATH": f"{_REPO_ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "OMNIGENT_RUNNER_ID": runner_id,
        "OMNIGENT_RUNNER_TUNNEL_BINDING_TOKEN": binding_token,
        "OMNIGENT_RUNNER_PARENT_PID": str(os.getpid()),
        "OMNIGENT_DATABRICKS_EXTRA_HEADERS": json.dumps(identity_headers(owner)),
        "RUNNER_SERVER_URL": base_url,
        "OPENAI_BASE_URL": f"{mock_llm_url}/v1",
        "OPENAI_API_KEY": "mock-key",
    }
    log_path = tmp / "runner.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [runner_executable(), "-m", "omnigent.runner._entry"],
            env=apply_runner_env(env),
            cwd=compat_runner_cwd(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + _BOOT_TIMEOUT_S
            online, last = False, "not polled yet"
            while not online and time.monotonic() < deadline:
                if proc.poll() is not None:
                    last = f"runner exited early with code {proc.returncode}"
                    break
                online, last = _runner_online(base_url, runner_id, owner)
                if not online:
                    time.sleep(_HEALTH_POLL_INTERVAL_S)
            if not online:
                raise RuntimeError(
                    f"runner {runner_id} never came online for {owner}: {last}\n"
                    f"Runner log:\n{log_path.read_text(errors='replace')[-3000:]}"
                )
            yield runner_id
        finally:
            terminate_process(proc)


@contextmanager
def spawn_team_chat_server(
    tmp: Path,
    *,
    mock_llm_url: str,
    features: str | None,
    runner_owner: str | None,
) -> Iterator[TeamChatServer]:
    """Spawn a multi-user header-auth server, optionally with an owned runner.

    :param tmp: Per-module temp dir for the DB, logs and agent YAML.
    :param mock_llm_url: Base URL of the session-scoped mock LLM.
    :param features: ``OMNIGENT_FEATURES`` value, or ``None`` for no flags.
    :param runner_owner: Email that owns the runner, or ``None`` for no runner.
    """
    agent_yaml = tmp / "hello_world.yaml"
    agent_yaml.write_text(_TEST_AGENT_YAML)
    # isolated_local_server keeps its database at this path.
    database_uri = f"sqlite:///{tmp / 'chat.db'}"
    binding_token = secrets.token_urlsafe(32)
    server_env: dict[str, str | None] = {
        # Multi-user: every request names its user via X-Forwarded-Email.
        "OMNIGENT_LOCAL_SINGLE_USER": None,
        "OMNIGENT_FEATURES": features,
        "OPENAI_BASE_URL": f"{mock_llm_url}/v1",
        "OPENAI_API_KEY": "mock-key",
        "ANTHROPIC_API_KEY": "",
        # Accept exactly this fixture's runner tunnel.
        "OMNIGENT_RUNNER_TUNNEL_TOKEN": binding_token,
        # Lets tests mint agent accounts through POST /v1/chat/agent-accounts.
        "OMNIGENT_CHAT_AGENT_SECRET": secrets.token_urlsafe(32),
    }
    with isolated_local_server(
        tmp,
        env=server_env,
        extra_args=["--agent", str(agent_yaml)],
        health_timeout=_BOOT_TIMEOUT_S,
        poll_interval=_HEALTH_POLL_INTERVAL_S,
    ) as base_url:
        if runner_owner is None:
            yield TeamChatServer(
                base_url=base_url,
                runner_owner=None,
                runner_id=None,
                database_uri=database_uri,
            )
            return
        set_fallback_mock_llm(mock_llm_url, "gpt-4o-mini", "Mock LLM response.")
        with _owned_runner(
            base_url,
            owner=runner_owner,
            binding_token=binding_token,
            mock_llm_url=mock_llm_url,
            tmp=tmp,
        ) as runner_id:
            yield TeamChatServer(
                base_url=base_url,
                runner_owner=runner_owner,
                runner_id=runner_id,
                database_uri=database_uri,
            )


@pytest.fixture(scope="module")
def team_chat_server(
    built_spa: None,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TeamChatServer]:
    """Multi-user server with ``team_chat`` on and a mock-LLM runner owned by Anna."""
    with spawn_team_chat_server(
        tmp_path_factory.mktemp("team_chat_server"),
        mock_llm_url=mock_llm_server_url,
        features=TEAM_CHAT_FEATURE,
        runner_owner=ANNA,
    ) as server:
        yield server


@pytest.fixture(scope="module")
def team_chat_server_flag_off(
    built_spa: None,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[TeamChatServer]:
    """The same multi-user server with no feature flags and no runner."""
    with spawn_team_chat_server(
        tmp_path_factory.mktemp("team_chat_server_flag_off"),
        mock_llm_url=mock_llm_server_url,
        features=None,
        runner_owner=None,
    ) as server:
        yield server


@pytest.fixture
def page_as(browser: Browser) -> Iterator[Callable[..., Page]]:
    """Open a page in a fresh browser context that identifies as *email*.

    Each call gets its own context, so two users never share cookies or
    storage. The context proxies through *email*'s identity forwarder, so
    HTTP requests and the updates WebSocket both carry the identity header.
    Pass ``viewport={"width": ..., "height": ...}`` to size the context from
    its first paint. All contexts and forwarders close at teardown.
    """
    contexts: list[BrowserContext] = []
    forwarder_urls: dict[str, str] = {}

    with ExitStack() as forwarders:

        def _open(email: str, *, viewport: dict[str, int] | None = None) -> Page:
            if email not in forwarder_urls:
                forwarder_urls[email] = forwarders.enter_context(identity_forwarder(email))
            options: dict[str, Any] = {"proxy": {"server": forwarder_urls[email]}}
            if viewport is not None:
                options["viewport"] = viewport
            context = browser.new_context(**options)
            contexts.append(context)
            return context.new_page()

        yield _open
        for context in contexts:
            context.close()


def seed_channel(
    server: TeamChatServer,
    *,
    as_user: str,
    name: str | None = None,
    topic: str | None = None,
) -> dict[str, Any]:
    """Create a channel through ``POST /v1/chat/channels`` and return it.

    :param server: The team chat server.
    :param as_user: Email of the creating user.
    :param name: Channel name; defaults to a unique ``e2e-<hex>`` name.
    :param topic: Optional channel topic.
    """
    body: dict[str, Any] = {"name": name or f"e2e-{uuid.uuid4().hex[:8]}"}
    if topic is not None:
        body["topic"] = topic
    resp = httpx.post(
        f"{server.base_url}/v1/chat/channels",
        json=body,
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_bootstrap(server: TeamChatServer, *, as_user: str) -> dict[str, Any]:
    """``GET /v1/chat/bootstrap`` as *as_user*; also makes them a member of ``general``."""
    resp = httpx.get(
        f"{server.base_url}/v1/chat/bootstrap",
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()


def join_channel(server: TeamChatServer, *, as_user: str, channel_id: str) -> None:
    """Join a public channel through ``POST /v1/chat/channels/{id}/join``."""
    resp = httpx.post(
        f"{server.base_url}/v1/chat/channels/{channel_id}/join",
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()


def list_messages(
    server: TeamChatServer,
    *,
    as_user: str,
    channel_id: str,
    thread_root_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the newest page of ``GET /v1/chat/channels/{id}/messages?tail=1``."""
    params: dict[str, str] = {"tail": "true"}
    if thread_root_id is not None:
        params["thread_root_id"] = thread_root_id
    resp = httpx.get(
        f"{server.base_url}/v1/chat/channels/{channel_id}/messages",
        params=params,
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["items"]


def seed_message(
    server: TeamChatServer,
    *,
    as_user: str,
    channel_id: str,
    body: str,
    thread_root_id: str | None = None,
) -> dict[str, Any]:
    """Post a message through ``POST /v1/chat/channels/{id}/messages``.

    :param server: The team chat server.
    :param as_user: Email of the author.
    :param channel_id: Target channel id.
    :param body: Message body.
    :param thread_root_id: Root message id to post a thread reply.
    """
    payload: dict[str, Any] = {"body": body, "client_id": f"seed-{uuid.uuid4().hex}"}
    if thread_root_id is not None:
        payload["thread_root_id"] = thread_root_id
    resp = httpx.post(
        f"{server.base_url}/v1/chat/channels/{channel_id}/messages",
        json=payload,
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()


def seed_agent_account(
    server: TeamChatServer, *, as_user: str, display_name: str, handle: str
) -> dict[str, Any]:
    """Create an agent account owned by *as_user*; return its public record.

    The response's one-time token is dropped so it never reaches test output.
    """
    resp = httpx.post(
        f"{server.base_url}/v1/chat/agent-accounts",
        json={"display_name": display_name, "handle": handle},
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["account"]


def revoke_agent_account(server: TeamChatServer, *, as_user: str, account_id: str) -> None:
    """Revoke an agent account so it leaves the mention directory."""
    resp = httpx.post(
        f"{server.base_url}/v1/chat/agent-accounts/{account_id}/revoke",
        headers=identity_headers(as_user),
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
