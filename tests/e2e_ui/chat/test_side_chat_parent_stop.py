"""Stop session on a hosted parent must also stop its generic side chats.

A real host daemon launches one runner for the parent and a separate one for
the side chat, so the stop has to reach a runner the parent never shared.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from playwright.sync_api import Page, expect

from tests._helpers.session import post_session_bundle
from tests.e2e_ui.conftest import _build_hello_world_bundle, configure_mock_llm, open_right_rail

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSISTANT = '[data-testid="message-bubble"][data-role="assistant"]'
_SIDE_ANSWER = "The side chat finished its long answer."
_HOST_ENV_KEEP = frozenset(
    {"PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "TERM"}
)


@dataclass
class HostedParent:
    base_url: str
    mock_url: str
    host_id: str
    session_id: str
    runner_id: str
    workspace: Path
    host_log: Path
    children: list[str] = field(default_factory=list)


def _wait(
    check: Callable[[], Any],
    description: str,
    timeout: float = 60.0,
    proc: subprocess.Popen[bytes] | None = None,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"host daemon exited ({proc.returncode}) before {description}")
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {description}")


def _runner_online(base_url: str, runner_id: str) -> bool:
    response = httpx.get(f"{base_url}/v1/runners/{runner_id}/status", timeout=10.0)
    response.raise_for_status()
    return response.json().get("online") is True


def _session(base_url: str, session_id: str) -> dict[str, Any]:
    response = httpx.get(f"{base_url}/v1/sessions/{session_id}", timeout=10.0)
    response.raise_for_status()
    return response.json()


def _items(base_url: str, session_id: str) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{base_url}/v1/sessions/{session_id}/items",
        params={"limit": 100, "order": "asc"},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()["data"]


def _gate_pending(mock_url: str) -> bool:
    response = httpx.get(f"{mock_url}/gate/pending", timeout=5.0)
    response.raise_for_status()
    return bool(response.json()["pending"])


@pytest.fixture
def hosted_parent(
    live_server: str, mock_llm_server_url: str, tmp_path: Path
) -> Iterator[HostedParent]:
    """Run a real host daemon and create a hello_world parent it launches a runner for."""
    host_id = os.environ.get("OMNIGENT_E2E_HOST_ID") or uuid.uuid4().hex
    config_home = tmp_path / "host-config"
    config_home.mkdir()
    (config_home / "config.yaml").write_text(
        yaml.safe_dump({"host": {"host_id": host_id, "name": f"e2e-side-chat-{host_id[:12]}"}})
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {key: value for key, value in os.environ.items() if key in _HOST_ENV_KEEP}
    env.update(
        HOME=str(tmp_path / "host-home"),
        OMNIGENT_CONFIG_HOME=str(config_home),
        OMNIGENT_DATA_DIR=str(tmp_path / "host-data"),
        OMNIGENT_AUTH_PROVIDER="header",
        OMNIGENT_LOCAL_SINGLE_USER="1",
        OMNIGENT_DISABLE_CATALOG_LOOKUP="1",
        OMNIGENT_SKIP_ONBOARD="1",
        OMNIGENT_NO_UPDATE_CHECK="1",
        OPENAI_BASE_URL=f"{mock_llm_server_url}/v1",
        OPENAI_API_KEY="mock-key",
        PYTHONPATH=str(_REPO_ROOT),
        NO_PROXY="localhost,127.0.0.1,::1",
        no_proxy="localhost,127.0.0.1,::1",
    )
    (tmp_path / "host-home").mkdir()
    host_log = tmp_path / "host.log"
    with host_log.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "omnigent.host._daemon_entry", "--server", live_server],
            env=env,
            cwd=str(_REPO_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    parent: HostedParent | None = None
    try:

        def host_online() -> bool:
            hosts = httpx.get(f"{live_server}/v1/hosts", timeout=10.0).json()["hosts"]
            return any(h["host_id"] == host_id and h["status"] == "online" for h in hosts)

        _wait(host_online, "host daemon to register online", 90.0, proc)
        created = post_session_bundle(
            httpx.post,
            f"{live_server}/v1/sessions",
            _build_hello_world_bundle(),
            metadata={"host_id": host_id, "workspace": str(workspace), "title": "Hosted parent"},
            timeout=90.0,
        )
        created.raise_for_status()
        session_id = created.json()["session_id"]
        parent = HostedParent(
            base_url=live_server,
            mock_url=mock_llm_server_url,
            host_id=host_id,
            session_id=session_id,
            runner_id="",
            workspace=workspace,
            host_log=host_log,
        )

        def parent_runner_online() -> bool:
            runner_id = _session(live_server, session_id).get("runner_id")
            return bool(runner_id) and _runner_online(live_server, runner_id)

        _wait(parent_runner_online, "the parent's host-launched runner", 120.0, proc)
        parent.runner_id = _session(live_server, session_id)["runner_id"]
        yield parent
    finally:
        httpx.post(f"{mock_llm_server_url}/gate/release", timeout=5.0)
        if parent is not None:
            for session_id in (*parent.children, parent.session_id):
                httpx.delete(f"{live_server}/v1/sessions/{session_id}", timeout=60.0)
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def _confirm_host_import_review(page: Page) -> None:
    # A freshly opened hosted session shows a "Your setup is ready" review whose
    # overlay would swallow clicks on the Workspace rail toggle below it.
    dialog = page.get_by_role("dialog", name="Your setup is ready")
    try:
        expect(dialog).to_be_visible(timeout=15_000)
    except AssertionError:
        return
    dialog.get_by_role("button", name="Confirm").click()
    expect(dialog).to_be_hidden()


def _start_side_chat(page: Page, question: str) -> None:
    open_right_rail(page)
    rail = page.get_by_role("complementary", name="Workspace")
    rail.get_by_role("button", name="Open new", exact=True).click()
    page.get_by_role("menuitem", name="Side chat", exact=True).click()
    page.get_by_test_id("side-chat-input").fill(question)
    page.get_by_test_id("side-chat-send").click()


def _stop_parent_from_sidebar(page: Page, parent_id: str, artifacts: Path) -> int:
    row = page.locator("li").filter(has=page.locator(f'a[href="/c/{parent_id}"]'))
    expect(row).to_be_visible()
    row.hover()
    row.get_by_test_id("conversation-actions").click()
    page.get_by_test_id("stop-conversation").click()
    expect(page.get_by_role("dialog").get_by_text("Stop session?")).to_be_visible()
    page.screenshot(path=str(artifacts / "stop-session-dialog.png"))
    # Hold on the confirm dialog so the recording captures the Stop interaction.
    page.wait_for_timeout(3_500)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/v1/sessions/{parent_id}/events")
    ) as stop:
        page.get_by_test_id("stop-session-confirm").click()
    assert stop.value.request.post_data_json["type"] == "stop_session"
    return stop.value.status


def test_stop_session_stops_hosted_side_chat(
    request: pytest.FixtureRequest,
    hosted_parent: HostedParent,
    output_path: str,
) -> None:
    """Stopping the hosted parent must also stop the side chat's own runner and turn."""
    base_url, mock_url = hosted_parent.base_url, hosted_parent.mock_url
    parent_id = hosted_parent.session_id
    key = f"side-stop-{parent_id}"
    question = f"{key}: take a long time to answer this"
    configure_mock_llm(mock_url, [{"text": _SIDE_ANSWER, "block": True}], key=key, match=key)
    artifacts = Path(output_path)
    artifacts.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "parent_id": parent_id,
        "parent_runner_id": hosted_parent.runner_id,
        "host_id": hosted_parent.host_id,
        "samples_after_stop": [],
    }

    page: Page = request.getfixturevalue("page")
    pane = page.locator(".side-chat-backdrop")
    try:
        page.goto(f"{base_url}/c/{parent_id}")
        _confirm_host_import_review(page)
        # createSideChat may re-launch the parent runner first, so the fork can
        # land well after the send click; capture it off the response, not a route.
        with page.expect_response(
            lambda r: (
                r.request.method == "POST" and r.url.endswith(f"/v1/sessions/{parent_id}/fork")
            ),
            timeout=150_000,
        ) as fork_response:
            _start_side_chat(page, question)
        child_id = fork_response.value.json()["id"]
        hosted_parent.children.append(child_id)
        expect(pane.get_by_test_id("working-indicator")).to_be_visible(timeout=60_000)
        _wait(
            lambda: _gate_pending(mock_url),
            "the side chat's model request to block",
            120.0,
        )
        child = _session(base_url, child_id)
        child_runner = child["runner_id"]
        evidence.update(
            child_id=child_id, child_runner_id=child_runner, child_host_id=child["host_id"]
        )
        assert child["host_id"] == hosted_parent.host_id
        assert child_runner and child_runner != hosted_parent.runner_id
        _wait(lambda: _runner_online(base_url, child_runner), "the side chat's runner")
        page.screenshot(path=str(artifacts / "1-side-chat-working.png"))

        parent_runner = _session(base_url, parent_id)["runner_id"]
        evidence["parent_runner_id_at_stop"] = parent_runner
        evidence["stop_status"] = _stop_parent_from_sidebar(page, parent_id, artifacts)
        stopped_at = time.monotonic()
        _wait(
            lambda: not _runner_online(base_url, parent_runner),
            "parent runner offline",
            40.0,
        )
        evidence["parent_runner_offline_after_s"] = round(time.monotonic() - stopped_at, 2)
        evidence["stop_failure_toast"] = page.get_by_text("Couldn't stop the session").count()
        parent_offline_at = time.monotonic()
        page.screenshot(path=str(artifacts / "2-after-stop-session.png"))

        while time.monotonic() - parent_offline_at < 30.0:
            evidence["samples_after_stop"].append(
                {
                    "t_s": round(time.monotonic() - parent_offline_at, 1),
                    "child_runner_online": _runner_online(base_url, child_runner),
                    "child_status": _session(base_url, child_id)["status"],
                    "pane_working": pane.get_by_test_id("working-indicator").is_visible(),
                }
            )
            page.wait_for_timeout(2_000)
        child_online_after_stop = _runner_online(base_url, child_runner)
        evidence["child_runner_online_30s_after_parent_offline"] = child_online_after_stop

        httpx.post(f"{mock_url}/gate/release", timeout=5.0).raise_for_status()
        reply = pane.locator(_ASSISTANT).filter(has_text=_SIDE_ANSWER)
        reply_rendered = False
        try:
            expect(reply).to_be_visible(timeout=30_000)
            reply_rendered = True
        except AssertionError:
            pass
        evidence["reply_rendered_after_stop"] = reply_rendered
        evidence["child_after_release"] = {
            k: _session(base_url, child_id).get(k)
            for k in ("status", "runner_id", "last_task_error")
        }
        evidence["child_runner_online_after_release"] = _runner_online(base_url, child_runner)
        child_items = json.dumps(_items(base_url, child_id))
        evidence["child_history_keeps_question"] = question in child_items
        evidence["child_history_has_answer"] = _SIDE_ANSWER in child_items
        page.screenshot(path=str(artifacts / "3-side-chat-after-release.png"))
    finally:
        (artifacts / "journey-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")

    assert evidence["stop_status"] < 300 and evidence["stop_failure_toast"] == 0
    assert evidence["child_history_keeps_question"]
    assert not child_online_after_stop and not reply_rendered, (
        f"Stop session left the side chat running: runner {child_runner} online="
        f"{child_online_after_stop}, reply rendered after the stop={reply_rendered}"
    )
