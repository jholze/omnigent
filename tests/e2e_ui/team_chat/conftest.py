"""Fixtures for the team chat browser suite.

The shared ``live_server`` is single-user with no feature flags, and
``collaboration/_multi_user_server.py`` binds no runner. Team chat needs both:
a multi-user header-auth server with ``OMNIGENT_FEATURES=team_chat`` and a
mock-LLM runner owned by Anna, who starts agent threads. Each browser context
names its user with ``X-Forwarded-Email`` (as in
``collaboration/test_author_label.py``).

The seed helpers call the ``/v1/chat/*`` REST contract from the design. They
fail until those routes exist.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class TeamChatServer:
    """A running team chat server.

    :param base_url: Loopback base URL, e.g. ``"http://127.0.0.1:51234"``.
    :param runner_owner: Email of the user who owns the runner, or ``None``
        when the server was spawned without one.
    :param runner_id: The runner's id, or ``None`` without a runner.
    """

    base_url: str
    runner_owner: str | None
    runner_id: str | None


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
    }
    with isolated_local_server(
        tmp,
        env=server_env,
        extra_args=["--agent", str(agent_yaml)],
        health_timeout=_BOOT_TIMEOUT_S,
        poll_interval=_HEALTH_POLL_INTERVAL_S,
    ) as base_url:
        if runner_owner is None:
            yield TeamChatServer(base_url=base_url, runner_owner=None, runner_id=None)
            return
        set_fallback_mock_llm(mock_llm_url, "gpt-4o-mini", "Mock LLM response.")
        with _owned_runner(
            base_url,
            owner=runner_owner,
            binding_token=binding_token,
            mock_llm_url=mock_llm_url,
            tmp=tmp,
        ) as runner_id:
            yield TeamChatServer(base_url=base_url, runner_owner=runner_owner, runner_id=runner_id)


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
def page_as(browser: Browser) -> Iterator[Callable[[str], Page]]:
    """Open a page in a fresh browser context that identifies as *email*.

    Each call gets its own context, so two users never share cookies or
    storage. All contexts close at teardown.
    """
    contexts: list[BrowserContext] = []

    def _open(email: str) -> Page:
        context = browser.new_context(extra_http_headers=identity_headers(email))
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
