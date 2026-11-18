"""The fork's upstream-sync job reports a stuck sync PR instead of skipping silently."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fork/upstream-sync.sh"
HEALTH = ROOT / "scripts/fork/sync_pr_health.py"
NOW = dt.datetime(2026, 11, 18, 4, 0, tzinfo=dt.UTC)
GREEN = [
    {"name": "Python lint (ruff, pyrefly)", "conclusion": "SUCCESS"},
    {"name": "Web (lint, typecheck, vitest, build)", "conclusion": "SUCCESS"},
    {"name": "Pytest (SQLite)", "conclusion": "SUCCESS"},
]


def _health() -> Any:
    spec = importlib.util.spec_from_file_location("sync_pr_health", HEALTH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pr(**overrides: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "number": 149,
        "headRefName": "sync/upstream-20261031",
        "createdAt": "2026-11-16T18:38:18Z",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": GREEN,
        "url": "https://github.com/jholze/omnigent/pull/149",
    }
    pr.update(overrides)
    return pr


def test_no_sync_pr_is_not_classified() -> None:
    other = _pr(headRefName="feat/x")
    assert _health().classify([other], now=NOW, stale_days=7) is None


def test_fresh_green_sync_pr_is_open() -> None:
    state, summary, details = _health().classify([_pr()], now=NOW, stale_days=7)
    assert (state, summary) == ("open", "#149 sync/upstream-20261031")
    assert details["reasons"] == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"createdAt": "2026-10-31T18:38:18Z"}, "open for 17 days (limit 7)"),
        ({"mergeable": "CONFLICTING"}, "has merge conflicts"),
        (
            {"statusCheckRollup": [*GREEN[:2], {**GREEN[2], "conclusion": "FAILURE"}]},
            "failing checks: Pytest (SQLite)",
        ),
    ],
)
def test_stale_conflicting_or_red_sync_pr_is_blocked(
    overrides: dict[str, Any], reason: str
) -> None:
    state, summary, details = _health().classify([_pr(**overrides)], now=NOW, stale_days=7)
    assert state == "blocked"
    assert reason in summary
    assert details["reasons"] == [reason]


def test_non_required_check_failure_does_not_block() -> None:
    rollup = [*GREEN, {"name": "E2E UI", "conclusion": "FAILURE"}]
    state, _, _ = _health().classify([_pr(statusCheckRollup=rollup)], now=NOW, stale_days=7)
    assert state == "open"


class _Hook(BaseHTTPRequestHandler):
    received: list[tuple[str | None, dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.headers.get("Authorization"), json.loads(body)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return


def _fake_env(tmp_path: Path, pr_list: list[dict[str, Any]]) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/jholze/omnigent.git",
        ],
        check=True,
    )
    home = tmp_path / "home"
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(pr_list)}\nJSON\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "UPSTREAM_SYNC_STATUS_FILE": str(tmp_path / "status.json"),
        "UPSTREAM_SYNC_ENV_FILE": str(tmp_path / "upstream-sync.env"),
    }
    return repo, env


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is required")
def test_blocked_sync_pr_logs_writes_status_and_notifies(tmp_path: Path) -> None:
    stuck = _pr(createdAt="2026-10-31T18:38:18Z", mergeable="CONFLICTING")
    repo, env = _fake_env(tmp_path, [stuck])
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    secret = "test-key-must-not-leak"
    Path(env["UPSTREAM_SYNC_ENV_FILE"]).write_text(
        f"UPSTREAM_SYNC_WEBHOOK_URL=http://127.0.0.1:{server.server_port}/hook\n"
        f"UPSTREAM_SYNC_WEBHOOK_KEY={secret}\n"
    )
    _Hook.received = []
    try:
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--repo", str(repo)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()
    assert proc.returncode == 4, proc.stderr
    assert "WARNING: upstream sync is blocked: #149 sync/upstream-20261031" in proc.stdout
    assert "has merge conflicts" in proc.stdout
    assert "notify: webhook HTTP 200" in proc.stdout
    assert secret not in proc.stdout + proc.stderr
    status = json.loads(Path(env["UPSTREAM_SYNC_STATUS_FILE"]).read_text())
    assert status["state"] == "blocked"
    assert status["pr"]["number"] == 149
    assert status["pr"]["mergeable"] == "CONFLICTING"
    [(auth, payload)] = _Hook.received
    assert auth == f"Bearer {secret}"
    assert payload["source"] == "omnigent-upstream-sync"
    assert "#149" in payload["text"]


def test_healthy_open_sync_pr_exits_3_without_notifying(tmp_path: Path) -> None:
    created = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo, env = _fake_env(tmp_path, [_pr(createdAt=created)])
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 3, proc.stderr
    assert "open sync PR exists: #149 sync/upstream-20261031" in proc.stdout
    assert "notify:" not in proc.stdout
    assert json.loads(Path(env["UPSTREAM_SYNC_STATUS_FILE"]).read_text())["state"] == "open_pr"


def test_blocked_sync_pr_without_webhook_still_writes_status(tmp_path: Path) -> None:
    created = (dt.datetime.now(dt.UTC) - dt.timedelta(days=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo, env = _fake_env(tmp_path, [_pr(createdAt=created)])
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--repo", str(repo)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 4, proc.stderr
    assert "notify: no webhook configured" in proc.stdout
    assert json.loads(Path(env["UPSTREAM_SYNC_STATUS_FILE"]).read_text())["state"] == "blocked"
