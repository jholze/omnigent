"""Runner tunnel HTTP 401 recovery hint must be wrapper-aware and paste-safe.

Journey: a runner holding a stale login for a Databricks-workspace-hosted
server is launched through the ``isaac omni`` wrapper; the edge rejects its
tunnel upgrade with HTTP 401 three times and the runner exits with a recovery
hint, which the user pastes into zsh. The hint must name the configured
wrapper and quote the login URL: the display URL carries ``?o=<org>``, which a
nomatch shell (zsh by default) refuses to glob-expand.

Stand-ins: a loopback websockets server answering 401 at the upgrade (for the
Databricks edge), the runner child process the CLI spawns (for ``isaac omni``,
whose wrapper only sets ``OMNIGENT_WRAPPER_COMMAND``), and ``bash -O failglob``
(for zsh). ``isaac``/``omnigent`` are shimmed on PATH so the pasted command is
observed rather than executed.
"""

from __future__ import annotations

import asyncio
import http
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import serve

from omnigent.cli_invocation import WRAPPER_COMMAND_ENV
from omnigent.runner.identity import (
    RUNNER_ID_ENV_VAR,
    RUNNER_PARENT_PID_ENV_VAR,
    RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR,
    token_bound_runner_id,
)
from omnigent.util.server_url import ServerUrl
from tests._helpers.live_server import find_free_port

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = "isaac omni"
_ORG_ID = "2850744067564480"
_RUNNER_EXIT_TIMEOUT_S = 120
_REJECTION = "runner tunnel rejected by server (HTTP 401 persisted across 3 attempts)"
_HINT_RE = re.compile(r"run `(?P<command>[^`]+)` to re-authenticate")
# Ambient state that would leak into the runner and defeat the staged stale
# login, plus proxies that cannot reach the loopback stand-in.
_ENV_TO_CLEAR = (
    "OMNIGENT_DATA_DIR",
    "OMNIGENT_CONFIG_HOME",
    "OMNIGENT_REMOTE_AUTH_TOKEN",
    "RUNNER_SERVER_URL",
    "OMNIGENT_RUNNER_ID",
    "OMNIGENT_RUNNER_TUNNEL_BINDING_TOKEN",
    "OMNIGENT_RUNNER_INITIAL_AUTH_TOKEN",
    "OMNIGENT_RUNNER_DELEGATED_AUTH",
    "OMNIGENT_RUNNER_SLICE_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
)


@dataclass(frozen=True)
class RejectedRunner:
    """What the user is left with after the runner gives up."""

    api_base: str
    stderr: str
    returncode: int
    upgrade_attempts: int

    @property
    def hint(self) -> str:
        """The backticked command the exit message tells the user to run."""
        match = _HINT_RE.search(self.stderr)
        assert match, f"no login hint in runner output:\n{self.stderr}"
        return match.group("command")

    @property
    def display_url(self) -> str:
        return ServerUrl(api_base=self.api_base, org_id=_ORG_ID).display


class _RejectingEdge:
    """Loopback stand-in for a Databricks edge that 401s every tunnel upgrade."""

    def __init__(self) -> None:
        self.port = find_free_port()
        self.attempts: list[str] = []
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._stop = asyncio.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _process_request(self, connection: Any, request: Any) -> Any:
        self.attempts.append(request.headers.get("Authorization", ""))
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "Unauthorized\n")

    async def _serve(self) -> None:
        async def never_reached(connection: Any) -> None:
            del connection

        async with serve(
            never_reached, "127.0.0.1", self.port, process_request=self._process_request
        ):
            self._ready.set()
            await self._stop.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.close()

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(10), "stand-in edge did not start"

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=10)


def _stage_stale_login(home: Path, api_base: str) -> dict[str, str]:
    """Leave the machine the way a lapsed `omnigent login` does, isolated under *home*."""
    state = home / ".omnigent"
    state.mkdir(parents=True)
    (state / "auth_tokens.json").write_text(
        json.dumps(
            {
                api_base: {
                    "token": "stale-session-token",
                    "expires_at": time.time() + 3600,
                    "org_id": _ORG_ID,
                }
            }
        )
    )
    (home / ".databrickscfg").write_text("")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DATABRICKS_") and key not in _ENV_TO_CLEAR
    }
    env.update(
        {
            "HOME": str(home),
            "OMNIGENT_DATA_DIR": str(state),
            "DATABRICKS_CONFIG_FILE": str(home / ".databrickscfg"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            WRAPPER_COMMAND_ENV: _WRAPPER,
            "PYTHONPATH": os.pathsep.join([str(_REPO_ROOT), env.get("PYTHONPATH", "")]),
        }
    )
    return env


def _launch_runner(
    env: dict[str, str], api_base: str, cwd: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    """Spawn the runner the way ``omnigent/cli.py::_start_cli_runner_process`` does."""
    binding_token = secrets.token_urlsafe(32)
    env = {
        **env,
        "RUNNER_SERVER_URL": api_base,
        RUNNER_ID_ENV_VAR: token_bound_runner_id(binding_token),
        RUNNER_TUNNEL_BINDING_TOKEN_ENV_VAR: binding_token,
        RUNNER_PARENT_PID_ENV_VAR: str(os.getpid()),
    }
    with log_path.open("wb") as log_fh:
        return subprocess.Popen(
            [sys.executable, "-P", "-m", "omnigent.runner._entry"],
            env=env,
            cwd=str(cwd),
            stdout=log_fh,
            stderr=log_fh,
        )


@pytest.fixture(scope="module")
def rejected_runner(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RejectedRunner]:
    """Drive the journey once: stale login -> runner launch -> three 401s -> exit."""
    edge = _RejectingEdge()
    edge.start()
    api_base = f"http://127.0.0.1:{edge.port}/api/2.0/omnigent"
    home = tmp_path_factory.mktemp("home")
    log_path = home / "runner.log"
    try:
        proc = _launch_runner(_stage_stale_login(home, api_base), api_base, home, log_path)
        try:
            returncode = proc.wait(timeout=_RUNNER_EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(f"runner did not exit after the 401 rejections:\n{log_path.read_text()}")
        result = RejectedRunner(
            api_base=api_base,
            stderr=log_path.read_text(errors="replace"),
            returncode=returncode,
            upgrade_attempts=len(edge.attempts),
        )
    finally:
        edge.stop()
    assert result.returncode == 1 and _REJECTION in result.stderr, (
        f"journey did not reach the reported rejection (exit {result.returncode}):\n"
        f"{result.stderr}"
    )
    assert result.upgrade_attempts == 3, edge.attempts
    yield result


def test_rejection_hint_names_the_configured_wrapper(rejected_runner: RejectedRunner) -> None:
    hint = rejected_runner.hint
    assert hint.startswith(f"{_WRAPPER} login "), (
        f"hint names the bare CLI although {WRAPPER_COMMAND_ENV}={_WRAPPER!r}: `{hint}`"
    )


def test_rejection_hint_login_url_survives_a_nomatch_shell(
    rejected_runner: RejectedRunner, tmp_path: Path
) -> None:
    hint = rejected_runner.hint
    expected_url = rejected_runner.display_url
    assert expected_url in hint, f"hint does not show the ?o= display URL: `{hint}`"

    shims = tmp_path / "bin"
    shims.mkdir()
    for name in ("isaac", "omnigent"):
        shim = shims / name
        shim.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        shim.chmod(0o755)
    pasted = subprocess.run(
        ["bash", "-O", "failglob", "-c", hint],
        env={"PATH": f"{shims}{os.pathsep}/usr/bin:/bin"},
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert pasted.returncode == 0 and expected_url in pasted.stdout.splitlines(), (
        f"pasting `{hint}` into a nomatch shell did not invoke login with the URL intact:\n"
        f"{pasted.stderr}{pasted.stdout}"
    )
