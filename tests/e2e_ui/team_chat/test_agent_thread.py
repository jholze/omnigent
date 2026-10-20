"""E2E: an Agent-Thread in the team chat, in real browsers (T-008).

Each test links a real session to a channel thread the way the agent-thread
start does: the session carries the chat-thread label, channel members get
READ, and ``chat_threads`` points at the session. The agent's side is driven
through the server's external-harness events, the same ``POST /events``
calls a native harness forwarder makes: ``external_session_status``,
``external_output_text_delta`` and ``external_assistant_message``. Status
edges run the server's real chat mirror, so a finished turn posts the reply
and the ``thread.updated`` frame, and a failed turn posts the system line.
Approval cards come from the session's real permission-request hook, which
parks an elicitation until someone answers.

Not used, because they do not work on this server today:

- The mention start cannot bind a session (no host: ``host_offline``; with a
  host it needs a workspace, and the start passes no agent cache for its
  validation).
- The team chat runner fixture never answers the session-init request, so a
  mock-LLM turn cannot run.

Chip and card rules asserted here:

- "braucht Antwort" needs an open approval, with status ``waiting`` or
  ``running`` (AC-044a, AC-047a). ``waiting`` without one shows
  "arbeitet im Hintergrund" (AC-044c).
- ``response.elicitation_resolved`` carries ``decided_by``, so a card answered
  elsewhere names the resolver (AC-057a), as the viewer's own resolve does
  (AC-056a).
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from playwright.sync_api import Locator, Page, expect

from tests._helpers.session import bundle_files, post_session_bundle
from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    fetch_bootstrap,
    identity_headers,
    join_channel,
    list_messages,
    seed_channel,
    seed_message,
)

BOB = "bob@team-chat.test"
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
_AGENT_NAME = "chat_agent"
_AGENT_YAML = f"""\
spec_version: 1
name: {_AGENT_NAME}
prompt: You answer in the team chat.

executor:
  model: gpt-4o-mini
  config:
    harness: openai-agents
"""
_CHAT_THREAD_LABEL = "omnigent.chat.v1"
_UPDATES_SOCKET_PATH = "/v1/sessions/updates"
_LEVEL_READ = 1
_LEVEL_EDIT = 2
_MIN_TOUCH_TARGET_PX = 44
_APPROVAL_TEXT = "Migration anwenden?"
_READ_ONLY_NOTE = "Nur Aufrufer und Bearbeiter können antworten"
_LIVE_TESTID = "agent-thread-live"
_TOAST = "[data-sonner-toast]"
_INBOX_ITEM = '[data-testid="inbox-item"]'
_RESOLVE_URL = re.compile(r"/v1/sessions/[^/]+/elicitations/[^/]+/resolve$")
_LOAD_TIMEOUT_MS = 30_000
_SERVER_TIMEOUT_S = 30.0


@dataclass
class AgentThread:
    """A channel thread linked to a real agent session.

    :param token: Unique text in the root message, used to find its row.
    """

    channel_id: str
    root_id: str
    session_id: str
    token: str


@dataclass
class ParkedApproval:
    """A permission request the hook route holds open until it is answered."""

    session_id: str
    elicitation_id: str
    verdict: dict[str, Any] = field(default_factory=dict)
    thread: threading.Thread | None = None


def _request(
    server: TeamChatServer,
    method: str,
    path: str,
    *,
    as_user: str,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    resp = httpx.request(
        method,
        f"{server.base_url}{path}",
        json=body,
        headers=identity_headers(as_user),
        timeout=30.0,
        trust_env=False,
    )
    resp.raise_for_status()
    return resp


def _wait_until(predicate: Callable[[], bool], *, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {what}")


def _agent_channel(server: TeamChatServer) -> dict[str, Any]:
    """A channel Anna created; Jens and Bob are members."""
    channel = seed_channel(server, as_user=ANNA)
    for user in (JENS, BOB):
        fetch_bootstrap(server, as_user=user)
        join_channel(server, as_user=user, channel_id=channel["id"])
    return channel


def _agent_event(server: TeamChatServer, thread: AgentThread, kind: str, **data: Any) -> None:
    """Post one external-harness event for the thread's session, as its owner."""
    _request(
        server,
        "POST",
        f"/v1/sessions/{thread.session_id}/events",
        as_user=ANNA,
        body={"type": kind, "data": data},
    )


def _set_status(server: TeamChatServer, thread: AgentThread, status: str, **data: Any) -> None:
    _agent_event(server, thread, "external_session_status", status=status, **data)


def _answer(server: TeamChatServer, thread: AgentThread, text: str, response_id: str) -> None:
    """The agent finishes a turn with *text*; the server mirrors it into the thread."""
    _agent_event(
        server,
        thread,
        "external_assistant_message",
        agent=_AGENT_NAME,
        text=text,
        response_id=response_id,
    )
    _set_status(server, thread, "idle", response_id=response_id)


def _link_thread(server: TeamChatServer, thread: AgentThread) -> None:
    """Point ``chat_threads`` at the session, as a finished agent-thread start does."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from omnigent.db.db_models import SqlChatThread, current_workspace_id
    from omnigent.db.utils import get_or_create_engine, now_epoch_us

    now = now_epoch_us()
    with Session(get_or_create_engine(server.database_uri)) as session:
        row = session.execute(
            select(SqlChatThread).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == thread.root_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = SqlChatThread(
                workspace_id=current_workspace_id(),
                root_message_id=thread.root_id,
                channel_id=thread.channel_id,
                reply_count=0,
                participants="[]",
            )
            session.add(row)
        row.session_id = thread.session_id
        row.agent_handle = "agent"
        row.agent_state = "active"
        row.caller_id = ANNA
        row.start_requested_at = now
        row.updated_at = now
        session.commit()


def _thread_replies(server: TeamChatServer, thread: AgentThread) -> list[dict[str, Any]]:
    return list_messages(
        server, as_user=ANNA, channel_id=thread.channel_id, thread_root_id=thread.root_id
    )


def _start_agent_thread(server: TeamChatServer, channel_id: str) -> AgentThread:
    """Root message, linked session, READ for members, and the agent's first reply.

    The mirrored first reply sends ``thread.updated`` with the session id to
    every member's open page, so open viewer pages before calling this.
    """
    token = f"t008-{uuid.uuid4().hex[:10]}"
    root = seed_message(
        server,
        as_user=ANNA,
        channel_id=channel_id,
        body=f"Agent, bitte die Tests laufen lassen. {token}",
    )
    label = json.dumps({"channel_id": channel_id, "thread_root_id": root["id"]})
    created = post_session_bundle(
        httpx.post,
        f"{server.base_url}/v1/sessions",
        bundle_files({"config.yaml": _AGENT_YAML.encode()}),
        metadata={"labels": {_CHAT_THREAD_LABEL: label}},
        headers=identity_headers(ANNA),
        timeout=30.0,
        trust_env=False,
    )
    created.raise_for_status()
    thread = AgentThread(channel_id, root["id"], str(created.json()["session_id"]), token)
    for user in (JENS, BOB):
        _request(
            server,
            "PUT",
            f"/v1/sessions/{thread.session_id}/permissions",
            as_user=ANNA,
            body={"user_id": user, "level": _LEVEL_READ},
        )
    _link_thread(server, thread)

    _set_status(server, thread, "running", response_id="resp_first")
    _answer(server, thread, "Bin dran.", "resp_first")
    _wait_until(
        lambda: any(
            reply.get("author_kind") == "omnigent_agent"
            for reply in _thread_replies(server, thread)
        ),
        timeout_s=_SERVER_TIMEOUT_S,
        what="the agent's first reply in the thread",
    )
    return thread


def _pending_ids(server: TeamChatServer, session_id: str) -> set[str]:
    snapshot = _request(server, "GET", f"/v1/sessions/{session_id}", as_user=ANNA).json()
    return {
        str(entry.get("elicitation_id")) for entry in snapshot.get("pending_elicitations") or []
    }


def _park(server: TeamChatServer, approval: ParkedApproval) -> None:
    """Long-poll the hook route as Anna; it returns once someone answers."""
    payload = {
        "session_id": "claude_sess_t008",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/tmp",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "alembic upgrade head", "description": _APPROVAL_TEXT},
        "_omnigent_elicitation_id": approval.elicitation_id,
    }
    try:
        resp = httpx.post(
            f"{server.base_url}/v1/sessions/{approval.session_id}/hooks/permission-request",
            json=payload,
            headers=identity_headers(ANNA),
            timeout=180.0,
            trust_env=False,
        )
        approval.verdict["status"] = resp.status_code
        approval.verdict["body"] = resp.text
    except httpx.HTTPError as exc:
        approval.verdict["error"] = repr(exc)


@contextlib.contextmanager
def _parked_approval(server: TeamChatServer, session_id: str) -> Iterator[ParkedApproval]:
    """Ask for approval of "Migration anwenden?" in *session_id* until the block ends."""
    approval = ParkedApproval(session_id, f"elicit_claude_{secrets.token_hex(16)}")
    approval.thread = threading.Thread(target=_park, args=(server, approval), daemon=True)
    approval.thread.start()
    try:
        _wait_until(
            lambda: approval.elicitation_id in _pending_ids(server, session_id),
            timeout_s=_SERVER_TIMEOUT_S,
            what="the approval request to be pending",
        )
        yield approval
    finally:
        if approval.thread.is_alive():
            with contextlib.suppress(httpx.HTTPError):
                _request(
                    server,
                    "POST",
                    f"/v1/sessions/{session_id}/elicitations/{approval.elicitation_id}/resolve",
                    as_user=ANNA,
                    body={"action": "decline"},
                )
            approval.thread.join(timeout=30.0)


def _open_channel(page: Page, server: TeamChatServer, channel_id: str) -> None:
    """Load the channel and wait until the updates socket that carries chat frames opens."""
    with page.expect_websocket(
        lambda socket: _UPDATES_SOCKET_PATH in socket.url, timeout=_LOAD_TIMEOUT_MS
    ):
        page.goto(f"{server.base_url}/chat/{quote(channel_id, safe='')}")
    expect(page.get_by_test_id(CHAT_TEST_IDS["messageList"]).first).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )


def _root_row(page: Page, token: str) -> Locator:
    """The thread root in the channel column, not the copy in the thread pane."""
    return (
        page.get_by_test_id(CHAT_TEST_IDS["messageList"])
        .first.get_by_test_id(CHAT_TEST_IDS["message"])
        .filter(has_text=token)
    )


def _root_chip(page: Page, token: str) -> Locator:
    return _root_row(page, token).get_by_test_id(CHAT_TEST_IDS["agentStatusChip"])


def _open_thread(page: Page, token: str) -> Locator:
    """Open the root's thread in-app (no reload) and return the thread pane."""
    row = _root_row(page, token)
    expect(row).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    row.hover()
    row.get_by_test_id(CHAT_TEST_IDS["replyAction"]).click()
    pane = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(pane).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    return pane


def _open_live_thread(page: Page, thread: AgentThread) -> Locator:
    """Open the thread and wait until its session stream is subscribed.

    A status edge sent between the thread's snapshot and its stream
    subscription is not replayed, so tests send agent events only after this.
    """
    stream_path = f"/v1/sessions/{thread.session_id}/stream"
    with page.expect_response(
        lambda response: stream_path in response.url, timeout=_LOAD_TIMEOUT_MS
    ):
        return _open_thread(page, thread.token)


def _thread_chip(pane: Locator) -> Locator:
    return pane.get_by_role("status").get_by_test_id(CHAT_TEST_IDS["agentStatusChip"])


def _thread_card(pane: Locator) -> Locator:
    return pane.get_by_test_id(_LIVE_TESTID).get_by_test_id(CHAT_TEST_IDS["approvalCard"])


def _directory_label(server: TeamChatServer, principal_id: str) -> str:
    """The label the UI shows for *principal_id*: ``display_name``, else the id."""
    for entry in fetch_bootstrap(server, as_user=principal_id)["directory"]:
        if entry["id"] == principal_id and entry.get("display_name"):
            return str(entry["display_name"])
    return principal_id


def _assert_no_horizontal_scroll(page: Page) -> None:
    scroll_width, client_width = page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert scroll_width <= client_width, (
        f"page scrolls horizontally: scrollWidth={scroll_width} > clientWidth={client_width}"
    )


def _assert_touch_target(control: Locator, name: str) -> None:
    expect(control).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    box = control.bounding_box()
    assert box is not None, f"{name} has no box"
    assert box["height"] >= _MIN_TOUCH_TARGET_PX, f"{name} is {box['height']}px tall"
    assert box["width"] >= _MIN_TOUCH_TARGET_PX, f"{name} is {box['width']}px wide"


def test_ac_044a_open_thread_chip_shows_braucht_antwort_within_1s(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Anna's open thread: open approval, then waiting, shows "braucht Antwort" in 1 s."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    chip = _thread_chip(pane)
    expect(chip).to_have_text("Agent fertig", timeout=_LOAD_TIMEOUT_MS)

    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)
    with _parked_approval(team_chat_server, thread.session_id):
        _set_status(team_chat_server, thread, "waiting", response_id="resp_turn")
        expect(chip).to_have_text("braucht Antwort", timeout=1_000)


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
def test_ac_044c_waiting_without_approval_shows_arbeitet_im_hintergrund_within_1s(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Waiting with no open approval: "arbeitet im Hintergrund" in 1 s, page does not scroll."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    chip = _thread_chip(pane)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)

    _set_status(team_chat_server, thread, "waiting", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet im Hintergrund", timeout=1_000)
    expect(chip).not_to_have_text("braucht Antwort")
    _assert_no_horizontal_scroll(page)


def test_ac_044b_failed_turn_shows_fehler_chip_and_system_line_without_raw_text(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """A failed turn: chip "Fehler", one system line, and no raw provider error."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    expect(_thread_chip(pane)).to_have_text("Agent fertig", timeout=_LOAD_TIMEOUT_MS)

    raw_error = f"upstream exploded: HTTP 500 from provider ({thread.token})"
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    _set_status(team_chat_server, thread, "failed", response_id="resp_turn", output=raw_error)

    expect(_thread_chip(pane)).to_have_text("Fehler", timeout=_LOAD_TIMEOUT_MS)
    system_lines = pane.locator(
        f'[data-testid="{CHAT_TEST_IDS["message"]}"][data-author-kind="system"]'
    )
    expect(system_lines).to_have_count(1, timeout=_LOAD_TIMEOUT_MS)
    expect(page.get_by_text("upstream exploded")).to_have_count(0)


def test_ac_045a_read_viewer_sees_streamed_text_grow_without_reload(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens (READ) watches "Tests sind grün" arrive in five parts in one page load."""
    channel = _agent_channel(team_chat_server)
    page = page_as(JENS)
    _open_channel(page, team_chat_server, channel["id"])
    page.evaluate("() => { window.__t008NoReload = true; }")
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    expect(_thread_chip(pane)).to_have_text("Agent fertig", timeout=_LOAD_TIMEOUT_MS)

    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    live = pane.get_by_test_id(_LIVE_TESTID)
    expect(live).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    parts = ["Gute ", "Nachricht: ", "Tests ", "sind ", "grün"]
    shown = ""
    for index, part in enumerate(parts):
        _agent_event(
            team_chat_server,
            thread,
            "external_output_text_delta",
            delta=part,
            message_id="msg_turn",
            index=index,
        )
        shown += part
        expect(live).to_contain_text(shown.strip(), timeout=5_000)
        if index < len(parts) - 1:
            assert "grün" not in live.inner_text(), "the rest arrived before it was sent"

    _answer(team_chat_server, thread, "".join(parts), "resp_turn")
    expect(pane).to_contain_text("Tests sind grün")
    assert page.evaluate("() => window.__t008NoReload === true"), "the page reloaded"


def test_ac_047a_channel_root_chip_shows_braucht_antwort_within_5s(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens's channel, no thread open: running, then waiting on an approval, in 5 s."""
    channel = _agent_channel(team_chat_server)
    page = page_as(JENS)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    chip = _root_chip(page, thread.token)
    expect(chip).to_have_text("Agent fertig", timeout=_LOAD_TIMEOUT_MS)

    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet", timeout=5_000)
    expect(page.get_by_test_id(CHAT_TEST_IDS["threadPane"])).to_have_count(0)
    with _parked_approval(team_chat_server, thread.session_id):
        _set_status(team_chat_server, thread, "waiting", response_id="resp_turn")
        expect(chip).to_have_text("braucht Antwort", timeout=5_000)


def test_ac_055a_open_thread_shows_approval_card_with_genehmigen_and_ablehnen(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """The agent asks "Migration anwenden?"; Anna's open thread shows the card inline."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(_thread_chip(pane)).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)

    with _parked_approval(team_chat_server, thread.session_id):
        card = _thread_card(pane)
        expect(card).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        expect(card).to_contain_text(_APPROVAL_TEXT)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible()
        expect(card.get_by_role("button", name="Ablehnen")).to_be_visible()


def test_ac_055b_approval_requested_while_thread_closed_shows_on_open(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """An approval asked while Anna's thread was closed is there when she opens it."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id):
        expect(page.get_by_test_id(CHAT_TEST_IDS["threadPane"])).to_have_count(0)
        pane = _open_live_thread(page, thread)
        card = _thread_card(pane)
        expect(card).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        expect(card).to_contain_text(_APPROVAL_TEXT)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible()


def test_ac_056a_genehmigen_resolves_202_and_card_names_anna(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Anna clicks "Genehmigen": resolve answers 202 and the card says "Genehmigt von <Anna>"."""
    channel = _agent_channel(team_chat_server)
    anna_label = _directory_label(team_chat_server, ANNA)
    page = page_as(ANNA)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id) as approval:
        card = _thread_card(pane)
        approve = card.get_by_role("button", name="Genehmigen")
        expect(approve).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        with page.expect_response(
            lambda response: (
                _RESOLVE_URL.search(response.url) is not None and response.request.method == "POST"
            )
        ) as resolved:
            approve.click()
        assert resolved.value.status == 202
        expect(card).to_contain_text(f"Genehmigt von {anna_label}", timeout=5_000)
        expect(card.get_by_role("button", name="Genehmigen")).to_have_count(0)
        assert approval.thread is not None
        approval.thread.join(timeout=30.0)
        assert "allow" in approval.verdict.get("body", ""), approval.verdict
    expect(page.locator(_TOAST)).to_have_count(0)


def test_ac_056b_read_viewer_card_has_no_buttons_and_says_who_may_answer(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Bob (READ) sees the open card without buttons and with the read-only note."""
    channel = _agent_channel(team_chat_server)
    page = page_as(BOB)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id):
        card = _thread_card(pane)
        expect(card).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        expect(card).to_contain_text(_READ_ONLY_NOTE)
        expect(card.get_by_role("button")).to_have_count(0)


def test_ac_057a_inbox_approval_by_jens_updates_annas_card_within_2s(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens (EDIT) approves in the InboxPage; Anna's card names him within 2 s."""
    channel = _agent_channel(team_chat_server)
    jens_label = _directory_label(team_chat_server, JENS)
    anna = page_as(ANNA)
    _open_channel(anna, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _request(
        team_chat_server,
        "PUT",
        f"/v1/sessions/{thread.session_id}/permissions",
        as_user=ANNA,
        body={"user_id": JENS, "level": _LEVEL_EDIT},
    )
    pane = _open_live_thread(anna, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id):
        card = _thread_card(pane)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible(
            timeout=_LOAD_TIMEOUT_MS
        )
        jens = page_as(JENS)
        jens.goto(f"{team_chat_server.base_url}/inbox")
        inbox_card = (
            jens.locator(_INBOX_ITEM)
            .filter(has_text=_APPROVAL_TEXT)
            .get_by_test_id(CHAT_TEST_IDS["approvalCard"])
        )
        expect(inbox_card).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        inbox_card.get_by_role("button", name="Approve", exact=True).click()

        expect(card).to_contain_text(f"Genehmigt von {jens_label}", timeout=2_000)
        expect(card.get_by_role("button", name="Genehmigen")).to_have_count(0)
    expect(anna.locator(_TOAST)).to_have_count(0)


def test_nfr032_desktop_1280_agent_thread_with_approval_does_not_scroll(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """At 1280x800 the root chip, thread chip and approval card fit without scrolling."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=DESKTOP)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id):
        card = _thread_card(pane)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible(
            timeout=_LOAD_TIMEOUT_MS
        )
        expect(_thread_chip(pane)).to_have_text("braucht Antwort")
        expect(_root_chip(page, thread.token)).to_have_text("braucht Antwort")
        _assert_no_horizontal_scroll(page)
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        box = card.bounding_box()
        assert box is not None
        assert box["x"] + box["width"] <= client_width, "approval card overflows the viewport"


def test_nfr032_phone_390_agent_thread_approval_buttons_are_44px(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """At 390x844 the card's buttons are 44px, the chip is no control, nothing scrolls."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=PHONE)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(_root_chip(page, thread.token)).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)
    _assert_no_horizontal_scroll(page)
    pane = _open_live_thread(page, thread)

    with _parked_approval(team_chat_server, thread.session_id):
        card = _thread_card(pane)
        _assert_touch_target(card.get_by_role("button", name="Genehmigen"), "Genehmigen")
        _assert_touch_target(card.get_by_role("button", name="Ablehnen"), "Ablehnen")
        chip = _thread_chip(pane)
        expect(chip).to_have_text("braucht Antwort")
        # A status, not a control: no touch target needed while this holds.
        assert chip.evaluate("el => el.closest('a,button,[role=button],[tabindex]') === null"), (
            "the status chip became a control; give it a 44px hit area"
        )
        _assert_no_horizontal_scroll(page)


def test_reload_after_thread_start_shows_live_chip_and_approval(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """A page opened after the start (no thread.updated seen) still shows live state."""
    channel = _agent_channel(team_chat_server)
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id):
        page = page_as(ANNA)
        _open_channel(page, team_chat_server, channel["id"])
        expect(_root_chip(page, thread.token)).to_have_text(
            "braucht Antwort", timeout=_LOAD_TIMEOUT_MS
        )
        pane = _open_live_thread(page, thread)
        card = _thread_card(pane)
        expect(card).to_contain_text(_APPROVAL_TEXT, timeout=_LOAD_TIMEOUT_MS)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
def test_nfr032_long_decided_by_wraps(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """A decider with no display name shows as its raw id; 80+ chars wrap inside the card."""
    decider = f"approver-{'x' * 80}@team-chat.test"
    expected_label = f"Genehmigt von {decider}"
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    _request(
        team_chat_server,
        "PUT",
        f"/v1/sessions/{thread.session_id}/permissions",
        as_user=ANNA,
        body={"user_id": decider, "level": _LEVEL_EDIT},
    )
    pane = _open_live_thread(page, thread)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")

    with _parked_approval(team_chat_server, thread.session_id) as approval:
        card = _thread_card(pane)
        expect(card.get_by_role("button", name="Genehmigen")).to_be_visible(
            timeout=_LOAD_TIMEOUT_MS
        )
        _request(
            team_chat_server,
            "POST",
            f"/v1/sessions/{thread.session_id}/elicitations/{approval.elicitation_id}/resolve",
            as_user=decider,
            body={"action": "accept"},
        )
        expect(card).to_have_attribute("data-state", "responded", timeout=_LOAD_TIMEOUT_MS)
        label = card.locator(f'span[title="{expected_label}"]')
        expect(label).to_have_attribute("aria-label", expected_label, timeout=_LOAD_TIMEOUT_MS)
        expect(label).to_have_text(expected_label)
        _assert_no_horizontal_scroll(page)
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        box = card.bounding_box()
        assert box is not None
        assert box["x"] + box["width"] <= client_width, "approval card overflows the viewport"


def _push_root_out_of_tail(server: TeamChatServer, channel_id: str, count: int = 51) -> None:
    """Post *count* newer top-level messages, so the root leaves the tail of 50.

    The posts rotate over the three members to stay under the per-member post limit.
    """
    authors = (JENS, BOB, ANNA)
    for index in range(count):
        seed_message(
            server,
            as_user=authors[index % len(authors)],
            channel_id=channel_id,
            body=f"newer message {index}",
        )


def _pending_strip(page: Page, thread: AgentThread) -> Locator:
    return page.get_by_test_id(CHAT_TEST_IDS["pendingRequest"]).filter(has_text=thread.token)


def _open_strip_with_root_outside_tail(
    server: TeamChatServer, page_as, viewport: dict[str, int]
) -> tuple[Page, AgentThread, Locator]:
    """Anna loads a channel whose waiting agent root is older than the tail of 50."""
    channel = _agent_channel(server)
    thread = _start_agent_thread(server, channel["id"])
    _push_root_out_of_tail(server, channel["id"])
    _set_status(server, thread, "running", response_id="resp_turn")
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, server, channel["id"])
    expect(_root_row(page, thread.token)).to_have_count(0)
    return page, thread, _pending_strip(page, thread)


def _approve_in_thread(page: Page) -> None:
    card = _thread_card(page.get_by_test_id(CHAT_TEST_IDS["threadPane"]))
    approve = card.get_by_role("button", name="Genehmigen")
    expect(approve).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    approve.click()
    expect(card).to_have_attribute("data-state", "responded", timeout=_LOAD_TIMEOUT_MS)


def test_ac_107b_desktop_1280_strip_names_root_outside_tail_and_clears_on_resolve(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """1280x800: strip row for a root outside the tail opens the thread and goes on resolve."""
    page, thread, strip = _open_strip_with_root_outside_tail(team_chat_server, page_as, DESKTOP)

    with _parked_approval(team_chat_server, thread.session_id):
        _set_status(team_chat_server, thread, "waiting", response_id="resp_turn")
        expect(strip).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
        expect(strip).to_have_attribute("data-root-id", thread.root_id)
        expect(strip).to_contain_text("Agent braucht Antwort")
        _assert_no_horizontal_scroll(page)

        strip.click()
        expect(page).to_have_url(re.compile(f"/t/{re.escape(thread.root_id)}$"))
        _approve_in_thread(page)
        expect(strip).to_have_count(0, timeout=_LOAD_TIMEOUT_MS)


def test_ac_107b_phone_390_strip_names_root_outside_tail_and_clears_on_resolve(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """390x844: 44px strip row opens the thread; back in the channel it is gone after resolve."""
    page, thread, strip = _open_strip_with_root_outside_tail(team_chat_server, page_as, PHONE)

    with _parked_approval(team_chat_server, thread.session_id):
        _set_status(team_chat_server, thread, "waiting", response_id="resp_turn")
        _assert_touch_target(strip, "pending request strip")
        expect(strip).to_have_attribute("data-root-id", thread.root_id)
        _assert_no_horizontal_scroll(page)

        strip.click()
        expect(page).to_have_url(re.compile(f"/t/{re.escape(thread.root_id)}$"))
        _approve_in_thread(page)
        page.go_back()
        expect(page.get_by_test_id(CHAT_TEST_IDS["messageList"]).first).to_be_visible(
            timeout=_LOAD_TIMEOUT_MS
        )
        expect(strip).to_have_count(0, timeout=_LOAD_TIMEOUT_MS)
