"""``omnigent host`` against a local server while ambient Databricks discovery never returns.

Reconstructs the reported journey: ``DATABRICKS_CONFIG_PROFILE`` is unset,
``~/.databrickscfg`` holds only a ``[DEFAULT]`` OAuth (``databricks-cli``)
profile, and the ``databricks auth token`` call the SDK's default credential
chain makes for it never returns (a stand-in for a CLI stuck on a token
refresh). The host is started the way the desktop shell starts it
(``omnigent host --server <url> --non-interactive``) in a real PTY and must
still register with the local server, which needs no Databricks credential.

Usage::

    python -m pytest tests/e2e/test_host_stalled_databricks_discovery_e2e.py -v
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import signal
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

pexpect = pytest.importorskip("pexpect")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Never contacted: the stub CLI stalls before any network call.
_WORKSPACE_URL = "https://stalled-workspace.cloud.databricks.com"

# Well above the desktop shell's 30 s connect timeout, with headroom for a
# bounded (not skipped) credential probe.
_REGISTER_TIMEOUT_S = 90.0

_ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
_CONNECTED_RE = re.compile(rb"Connected as '[^']+' \(([0-9a-f]{32})\)")


def _stage_home(home: Path) -> str:
    """Seed a host ``$HOME`` whose only Databricks credential is a token-less
    ``[DEFAULT]`` OAuth profile; returns the pre-seeded host id."""
    omni_dir = home / ".omnigent"
    omni_dir.mkdir(parents=True)
    host_id = uuid.uuid4().hex
    (omni_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"host": {"host_id": host_id, "name": f"e2e-stalled-discovery-{host_id[:8]}"}},
            sort_keys=True,
        )
    )
    (home / ".databrickscfg").write_text(
        f"[DEFAULT]\nhost = {_WORKSPACE_URL}\nauth_type = databricks-cli\n"
    )
    return host_id


def _stage_stalled_databricks_cli(bin_dir: Path) -> Path:
    """Install a ``databricks`` stub whose ``auth token`` blocks for as long as its caller lives.

    :returns: The log every invocation of the stub appends its arguments to.
    """
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "databricks"
    invocations = bin_dir / "invocations.log"
    body = (
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{invocations}"\n'
        'if [ "$1" = auth ] && [ "$2" = token ]; then\n'
        '  while kill -0 "$PPID" 2>/dev/null; do sleep 1; done\n'
        "  exit 1\n"
        "fi\n"
        "echo '{}'\n"
    )
    # The SDK only trusts a `databricks` binary larger than 1 MiB.
    pad = "# " + "x" * 78 + "\n"
    stub.write_text(body + pad * (1100 * 1024 // len(pad)))
    stub.chmod(0o755)
    return invocations


def _host_env(home: Path, bin_dir: Path) -> dict[str, str]:
    """Subprocess env: isolated ``$HOME``, stub CLI first on PATH, no ``DATABRICKS_*``."""
    env = os.environ.copy()
    for var in [name for name in env if name.startswith("DATABRICKS_")]:
        del env[var]
    env["HOME"] = str(home)
    env["OMNIGENT_CONFIG_HOME"] = str(home / ".omnigent")
    env.pop("OMNIGENT_DATA_DIR", None)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["NO_COLOR"] = "1"
    env["TERM"] = "xterm"
    pythonpath = [
        str(_REPO_ROOT),
        str(_REPO_ROOT / "sdks" / "python-client"),
        str(_REPO_ROOT / "sdks" / "ui"),
    ]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return env


def _host_status(client: httpx.Client, host_id: str) -> str | None:
    resp = client.get("/v1/hosts")
    for host in resp.json().get("hosts", []):
        if host["host_id"] == host_id:
            return host["status"]
    return None


def _stalled_cli_processes() -> list[str]:
    found: list[str] = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        with contextlib.suppress(OSError):
            args = cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if "databricks auth token" in args:
                found.append(f"{cmdline.parent.name}: {args.strip()}")
    return found


def test_host_registers_when_ambient_databricks_discovery_never_returns(
    live_server: str,
    http_client: httpx.Client,
    tmp_path: Path,
) -> None:
    """A host whose ambient Databricks credential discovery stalls still registers
    with a local server within the window the desktop shell waits for it."""
    home = tmp_path / "home"
    home.mkdir()
    host_id = _stage_home(home)
    bin_dir = tmp_path / "bin"
    cli_invocations = _stage_stalled_databricks_cli(bin_dir)

    child = pexpect.spawn(
        sys.executable,
        ["-m", "omnigent", "host", "--server", live_server, "--non-interactive", "--no-open"],
        env=_host_env(home, bin_dir),
        encoding=None,
        dimensions=(50, 200),
        timeout=_REGISTER_TIMEOUT_S,
        cwd=str(home),
    )
    console = io.BytesIO()
    child.logfile_read = console
    started = time.monotonic()
    try:
        try:
            child.expect(_CONNECTED_RE, timeout=_REGISTER_TIMEOUT_S)
        except (pexpect.TIMEOUT, pexpect.EOF) as exc:
            output = _ANSI_RE.sub(b"", console.getvalue()).decode("utf-8", "replace")
            pytest.fail(
                f"`omnigent host` did not register within {_REGISTER_TIMEOUT_S:.0f}s "
                f"({type(exc).__name__}; daemon alive={child.isalive()}; "
                f"server status={_host_status(http_client, host_id)!r}; "
                f"stalled CLI children={_stalled_cli_processes()!r}).\n"
                f"Console output:\n{output or '<none>'}"
            )
        elapsed = time.monotonic() - started
        assert child.match.group(1).decode() == host_id
        deadline = time.monotonic() + 15.0
        while _host_status(http_client, host_id) != "online":
            assert time.monotonic() < deadline, f"host {host_id} connected but never went online"
            time.sleep(0.5)
        assert elapsed < _REGISTER_TIMEOUT_S
        # A local server needs no Databricks credential, so registering with it
        # must never shell out to the Databricks CLI at all.
        assert not cli_invocations.exists(), (
            f"registering with a loopback server ran the Databricks CLI: "
            f"{cli_invocations.read_text()!r}"
        )
    finally:
        with contextlib.suppress(OSError):
            child.kill(signal.SIGINT)
        with contextlib.suppress(pexpect.TIMEOUT, pexpect.EOF):
            child.expect(pexpect.EOF, timeout=20)
        child.close(force=True)
