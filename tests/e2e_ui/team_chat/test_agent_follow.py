"""E2E: thread replies follow the agent session, and its sidebar visibility (T-121).

The thread is linked to a real session the way ``test_agent_thread.py`` does
it (the mention start cannot bind a session on this fixture). A reply typed
in the browser goes through the server's real follow path: an EDIT author's
reply takes the forward path with no "nicht an Agent" mark (AC-040a), a READ
author's reply is stored as ``not_forwarded`` and marked (AC-041a).

That linked session has no runner relay, so a forward ends ``failed``
(``runner_unavailable``). The ``[Name] text`` delivery is proven with a
mocked runner in ``tests/server/test_chat_agent_follow.py``; the strict xfail
below flips once this fixture can deliver. The forward state is set after the
POST's response is built, so marks are read after a reload. Bodies carry a
marker so failure output never needs the full text.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    fetch_bootstrap,
    join_channel,
    seed_channel,
    seed_message,
)
from tests.e2e_ui.team_chat.test_agent_thread import (
    _LOAD_TIMEOUT_MS,
    _SERVER_TIMEOUT_S,
    BOB,
    AgentThread,
    _agent_channel,
    _open_channel,
    _open_live_thread,
    _open_thread,
    _start_agent_thread,
    _thread_replies,
    _wait_until,
)
from tests.e2e_ui.team_chat.test_session_chat_ui import (
    _FILTER,
    _FILTER_ALL,
    _shared_root_session,
    _sidebar_row,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_UNTRUSTED_OPEN = "<untrusted_chat_content>"
_UNTRUSTED_CLOSE = "</untrusted_chat_content>"


def _send_in_thread(pane: Locator, body: str) -> None:
    """Type *body* in the thread composer and press "Senden" (phones send by button)."""
    composer = pane.get_by_test_id(CHAT_TEST_IDS["composer"])
    composer.get_by_test_id(CHAT_TEST_IDS["composerInput"]).fill(body)
    composer.get_by_role("button", name="Senden").click()


def _stored_reply(server: TeamChatServer, thread: AgentThread, marker: str) -> dict[str, Any]:
    """The thread reply whose body carries *marker*, once its forward state settled."""
    found: dict[str, Any] = {}

    def _settled() -> bool:
        for reply in _thread_replies(server, thread):
            if marker in reply["body"] and reply.get("forward_state") not in (None, "forwarding"):
                found.update(reply)
                return True
        return False

    _wait_until(_settled, timeout_s=_SERVER_TIMEOUT_S, what=f"reply {marker} to settle")
    return found


def _session_user_texts(server: TeamChatServer, session_id: str) -> list[str]:
    """Chat texts of the session's user messages, unwrapped from the untrusted marker."""
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    page = SqlAlchemyConversationStore(server.database_uri).list_items(
        session_id, limit=100, order="asc"
    )
    texts: list[str] = []
    for item in page.data:
        data = item.data
        if getattr(item, "type", None) != "message" or getattr(data, "role", None) != "user":
            continue
        for block in data.content:
            raw = block.get("text") if isinstance(block, dict) else None
            if not isinstance(raw, str) or _UNTRUSTED_OPEN not in raw:
                continue
            start = raw.index(_UNTRUSTED_OPEN) + len(_UNTRUSTED_OPEN)
            texts.append(str(json.loads(raw[start : raw.rindex(_UNTRUSTED_CLOSE)])["text"]))
    return texts


def _reloaded_pane(page: Page) -> Locator:
    """Reload the open thread URL so replies carry their stored forward state."""
    page.reload()
    pane = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(pane).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    return pane


def _reply_row(pane: Locator, message_id: str) -> Locator:
    return pane.locator(
        f'[data-testid="{CHAT_TEST_IDS["message"]}"][data-message-id="{message_id}"]'
    )


@_VIEWPORTS
def test_ac_040a_edit_caller_reply_takes_forward_path_without_mark(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Anna (caller) replies "bitte auch Linter": it takes the forward path, no mark."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)

    marker = f"m040-{uuid.uuid4().hex[:8]}"
    _send_in_thread(pane, f"bitte auch Linter {marker}")

    reply = _stored_reply(team_chat_server, thread, marker)
    # "failed" is this fixture's missing runner relay; see the module docstring.
    assert reply["forward_state"] in ("forwarded", "failed"), (
        f"{marker}: state {reply['forward_state']}"
    )
    pane = _reloaded_pane(page)
    row = _reply_row(pane, reply["id"])
    expect(row).to_have_count(1, timeout=_LOAD_TIMEOUT_MS)
    expect(row.get_by_test_id(CHAT_TEST_IDS["forwardNotForwarded"])).to_have_count(0)
    _assert_no_horizontal_scroll(page)


@pytest.mark.xfail(
    strict=True,
    reason="fixture runner has no relay for linked sessions: forward is runner_unavailable",
)
def test_ac_040a_session_receives_anna_prefixed_reply(team_chat_server: TeamChatServer) -> None:
    """Anna's thread reply reaches the session as "[Anna] bitte auch Linter"."""
    channel = _agent_channel(team_chat_server)
    thread = _start_agent_thread(team_chat_server, channel["id"])
    marker = f"m040s-{uuid.uuid4().hex[:8]}"
    seed_message(
        team_chat_server,
        as_user=ANNA,
        channel_id=channel["id"],
        body=f"bitte auch Linter {marker}",
        thread_root_id=thread.root_id,
    )
    reply = _stored_reply(team_chat_server, thread, marker)
    assert reply["forward_state"] == "forwarded", f"{marker}: state {reply['forward_state']}"
    # A bool, so a failure names the marker and never prints session text.
    received = f"[Anna] bitte auch Linter {marker}" in _session_user_texts(
        team_chat_server, thread.session_id
    )
    assert received, f"session has no '[Anna] bitte auch Linter' line for {marker}"


@_VIEWPORTS
def test_ac_041a_read_viewer_reply_is_stored_not_forwarded_and_marked(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Bob (READ) replies in the active thread: stored, not forwarded, "nicht an Agent"."""
    channel = _agent_channel(team_chat_server)
    page = page_as(BOB, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)

    marker = f"m041-{uuid.uuid4().hex[:8]}"
    _send_in_thread(pane, f"Ich schau auch mal {marker}")

    reply = _stored_reply(team_chat_server, thread, marker)
    assert reply["forward_state"] == "not_forwarded", f"{marker}: state {reply['forward_state']}"
    leaked = any(
        marker in text for text in _session_user_texts(team_chat_server, thread.session_id)
    )
    assert not leaked, f"{marker} reached the session"
    pane = _reloaded_pane(page)
    row = _reply_row(pane, reply["id"])
    mark = row.get_by_test_id(CHAT_TEST_IDS["forwardNotForwarded"])
    expect(mark).to_have_text("nicht an Agent", timeout=_LOAD_TIMEOUT_MS)
    _assert_no_horizontal_scroll(page)


def _billing_agent_thread(server: TeamChatServer) -> AgentThread:
    """Anna's agent thread in a fresh "#billing-…" channel; Jens and Bob get READ."""
    channel = seed_channel(server, as_user=ANNA, name=f"billing-{uuid.uuid4().hex[:6]}")
    for user in (JENS, BOB):
        fetch_bootstrap(server, as_user=user)
        join_channel(server, as_user=user, channel_id=channel["id"])
    return _start_agent_thread(server, channel["id"])


def _show_all_sessions(page: Page) -> None:
    # The filter button only takes pointer events while its header is hovered.
    session_filter = page.locator(_FILTER)
    expect(session_filter).to_be_attached(timeout=_LOAD_TIMEOUT_MS)
    session_filter.hover()
    session_filter.click()
    page.locator(_FILTER_ALL).click()


def test_ac_085a_read_grantee_sidebar_hides_billing_session_and_thread_opens_it(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens (READ): no #billing agent session in the main sidebar; "Session öffnen" opens it.

    Desktop only: the phone sidebar is a drawer, and the phone "Session öffnen"
    touch target is covered by AC-052a in ``test_agent_controls.py``.
    """
    thread = _billing_agent_thread(team_chat_server)
    plain_id = _shared_root_session(team_chat_server, title=f"Geteilt {uuid.uuid4().hex[:8]}")

    page = page_as(JENS, viewport=DESKTOP)
    page.goto(f"{team_chat_server.base_url}/c/{plain_id}")
    _show_all_sessions(page)
    # Positive control: the shared list loaded with the plain session.
    expect(_sidebar_row(page, plain_id)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(_sidebar_row(page, thread.session_id)).to_have_count(0)

    _open_channel(page, team_chat_server, thread.channel_id)
    pane = _open_thread(page, thread.token)
    link = pane.get_by_test_id(CHAT_TEST_IDS["threadOpenSession"])
    expect(link).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    link.click()
    expect(page).to_have_url(f"{team_chat_server.base_url}/c/{thread.session_id}")
    expect(_sidebar_row(page, thread.session_id)).to_have_count(0)
