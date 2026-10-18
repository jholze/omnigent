"""E2E: the Session chat journey across two browser contexts.

Alice and Bob are both granted edit by the ``"local"`` owner. Only Alice
opens the Session chat column. Alice posts a line. Bob's column is closed,
so Bob's Session chat control shows the unread dot, and its tooltip holds
the line (the stream event is bodyless; the column re-reads ``GET /chat``).
Bob opens the column, sees the line, and replies. The reply reaches Alice's
open column without a reload. Alice then reloads the root ``/c/{id}`` and
opens a sub-agent of the session; both show the two lines in order, because
the lines are stored on the root.

The lines are human chat, not turns. Neither context may render them as a
``message-bubble`` in the agent transcript.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests.e2e_ui.conftest import _server_state

_COLUMN = '[data-testid="session-human-chat-column"]'
_UNREAD = '[data-testid="session-chat-unread"]'
_BUBBLE = '[data-testid="message-bubble"]'
_PLACEHOLDER = "Message the people in this session"


def _toggle(page: Page) -> Locator:
    """The Session chat control in the chat header."""
    return page.get_by_role("button", name="Session chat")


def _open_column(page: Page) -> Locator:
    """Open the Session chat column from the chat header and return it."""
    toggle = _toggle(page)
    expect(toggle).to_be_visible(timeout=15_000)
    toggle.click()
    column = page.locator(_COLUMN)
    expect(column).to_be_visible()
    return column


def _send(column: Locator, text: str) -> None:
    """Type a line into the column's draft and press Enter."""
    draft = column.get_by_placeholder(_PLACEHOLDER)
    draft.fill(text)
    draft.press("Enter")


def _seed_child(session_id: str) -> str:
    """Create a sub-agent conversation under ``session_id`` and return its id."""
    database_uri = _server_state.get("database_uri")
    if not database_uri:
        pytest.skip("seeding a sub-agent requires the isolated spawned server")
    store = SqlAlchemyConversationStore(str(database_uri))
    parent = store.get_conversation(session_id)
    assert parent is not None
    child = store.create_conversation(
        agent_id=parent.agent_id,
        kind="sub_agent",
        parent_conversation_id=session_id,
    )
    assert child.root_conversation_id == session_id
    return child.id


def test_session_chat_journey_unread_reply_reload_and_sub_agent(
    browser: Browser,
    seeded_session: tuple[str, str],
) -> None:
    """Closed column gets a dot and tooltip; reply is live; root and child both show the lines."""
    base_url, session_id = seeded_session
    run_tag = uuid.uuid4().hex[:8]
    alice_email = f"alice-chat-{run_tag}@example.com"
    bob_email = f"bob-chat-{run_tag}@example.com"
    for grantee in (alice_email, bob_email):
        httpx.put(
            f"{base_url}/v1/sessions/{session_id}/permissions",
            json={"user_id": grantee, "level": 2},
            timeout=10.0,
        ).raise_for_status()
    child_id = _seed_child(session_id)

    first = f"human-chat-{run_tag}"
    reply = f"human-reply-{run_tag}"
    alice_ctx = browser.new_context(extra_http_headers={"X-Forwarded-Email": alice_email})
    bob_ctx = browser.new_context(extra_http_headers={"X-Forwarded-Email": bob_email})
    try:
        alice = alice_ctx.new_page()
        bob = bob_ctx.new_page()
        alice.goto(f"{base_url}/c/{session_id}")
        bob.goto(f"{base_url}/c/{session_id}")

        # (a) Only Alice opens the column. Bob's stays closed.
        alice_column = _open_column(alice)
        bob_toggle = _toggle(bob)
        expect(bob_toggle).to_be_visible(timeout=15_000)
        expect(bob.locator(_COLUMN)).to_have_count(0)
        expect(bob_toggle.locator(_UNREAD)).to_have_count(0)
        # A reload would drop this flag; it must survive until Bob sees the dot.
        bob.evaluate("() => { window.__sessionChatNoReload = true; }")

        # (b) Alice sends a line. It is not an agent bubble.
        _send(alice_column, first)
        expect(alice_column).to_contain_text(first, timeout=15_000)
        expect(alice.locator(_BUBBLE, has_text=first)).to_have_count(0)

        # (c) Bob's closed control shows the unread dot and the line as its tooltip.
        expect(bob_toggle.locator(_UNREAD)).to_be_visible(timeout=30_000)
        expect(bob_toggle).to_have_attribute("title", first, timeout=15_000)
        expect(bob.locator(_COLUMN)).to_have_count(0)
        assert bob.evaluate("() => window.__sessionChatNoReload === true")
        expect(bob.locator(_BUBBLE, has_text=first)).to_have_count(0)

        # (d) Bob opens the column, sees the line, and replies.
        alice.evaluate("() => { window.__sessionChatNoReload = true; }")
        bob_column = _open_column(bob)
        expect(bob_column).to_contain_text(first, timeout=15_000)
        expect(bob_toggle.locator(_UNREAD)).to_have_count(0)
        _send(bob_column, reply)
        expect(bob_column).to_contain_text(reply, timeout=15_000)
        expect(bob.locator(_BUBBLE, has_text=reply)).to_have_count(0)

        # (e) The reply reaches Alice's open column without a reload.
        expect(alice_column).to_contain_text(reply, timeout=30_000)
        assert alice.evaluate("() => window.__sessionChatNoReload === true")
        expect(alice.locator(_BUBBLE, has_text=reply)).to_have_count(0)

        # (f) Alice reloads the root and sees both lines, in order.
        alice.goto(f"{base_url}/c/{session_id}")
        assert alice.evaluate("() => window.__sessionChatNoReload === undefined")
        root_column = _open_column(alice)
        expect(root_column.locator("li p")).to_have_text([first, reply], timeout=15_000)

        # (g) Alice opens a sub-agent of the session and sees both lines there.
        alice.goto(f"{base_url}/c/{child_id}")
        child_column = _open_column(alice)
        expect(child_column.locator("li p")).to_have_text([first, reply], timeout=15_000)
        expect(alice.locator(_BUBBLE, has_text=first)).to_have_count(0)
        expect(alice.locator(_BUBBLE, has_text=reply)).to_have_count(0)
    finally:
        alice_ctx.close()
        bob_ctx.close()


def test_session_chat_two_more_people(
    browser: Browser,
    seeded_session: tuple[str, str],
) -> None:
    """Cara and Dan join Alice and Bob. Both closed columns get the dot, Dan sees the line live, and Cara's reply reaches the open columns."""
    base_url, session_id = seeded_session
    run_tag = uuid.uuid4().hex[:8]
    names = ("alice", "bob", "cara", "dan")
    emails = {name: f"{name}-more-{run_tag}@example.com" for name in names}
    for email in emails.values():
        httpx.put(
            f"{base_url}/v1/sessions/{session_id}/permissions",
            json={"user_id": email, "level": 2},
            timeout=10.0,
        ).raise_for_status()

    first = f"human-four-{run_tag}"
    reply = f"human-four-reply-{run_tag}"
    contexts = {
        name: browser.new_context(extra_http_headers={"X-Forwarded-Email": emails[name]})
        for name in names
    }
    try:
        pages = {name: contexts[name].new_page() for name in names}
        for page in pages.values():
            page.goto(f"{base_url}/c/{session_id}", wait_until="domcontentloaded")
        for page in pages.values():
            expect(page.get_by_role("button", name="Session chat")).to_be_visible(timeout=45_000)

        alice_column = _open_column(pages["alice"])
        dan_column = _open_column(pages["dan"])
        for name in ("bob", "cara"):
            expect(_toggle(pages[name])).to_be_visible(timeout=15_000)
            expect(pages[name].locator(_COLUMN)).to_have_count(0)

        _send(alice_column, first)
        expect(alice_column).to_contain_text(first, timeout=15_000)
        expect(dan_column).to_contain_text(first, timeout=30_000)
        for name in ("bob", "cara"):
            toggle = _toggle(pages[name])
            expect(toggle.locator(_UNREAD)).to_be_visible(timeout=30_000)
            expect(toggle).to_have_attribute("title", first, timeout=15_000)
            expect(pages[name].locator(_COLUMN)).to_have_count(0)
        for page in pages.values():
            expect(page.locator(_BUBBLE, has_text=first)).to_have_count(0)

        pages["alice"].evaluate("() => { window.__sessionChatNoReload = true; }")
        pages["dan"].evaluate("() => { window.__sessionChatNoReload = true; }")
        cara_column = _open_column(pages["cara"])
        expect(cara_column).to_contain_text(first, timeout=15_000)
        expect(_toggle(pages["cara"]).locator(_UNREAD)).to_have_count(0)
        _send(cara_column, reply)

        expect(alice_column).to_contain_text(reply, timeout=30_000)
        expect(dan_column).to_contain_text(reply, timeout=30_000)
        assert pages["alice"].evaluate("() => window.__sessionChatNoReload === true")
        assert pages["dan"].evaluate("() => window.__sessionChatNoReload === true")
        for page in pages.values():
            expect(page.locator(_BUBBLE, has_text=reply)).to_have_count(0)

        bob_column = _open_column(pages["bob"])
        expect(bob_column.locator("li p")).to_have_text([first, reply], timeout=15_000)
    finally:
        for context in contexts.values():
            context.close()
