"""E2E: session chat column and Agent-Thread session visibility (T-113, NFR-032).

Covers the browser side only: the ChatHeader toggle opens the session chat
column on a shared root session and shows the stored lines (AC-029a); a
grantee does not get the Agent-Thread session in the main sidebar but opens
it from the Thread (AC-085a); the owner keeps the sidebar row with its glyph
(AC-085b). The route's access matrix stays in ``tests/server/test_session_chat.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

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
)
from tests.e2e_ui.team_chat.test_agent_thread import (
    _AGENT_YAML,
    _LEVEL_READ,
    _LOAD_TIMEOUT_MS,
    DESKTOP,
    PHONE,
    _agent_channel,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
    _open_channel,
    _open_thread,
    _request,
    _start_agent_thread,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_SIDEBAR = '[data-testid="sidebar-conversation-list"]'
_FILTER = '[data-testid="session-filter"]'
# This server lists no "Shared" scope; "All sessions" includes granted ones.
_FILTER_ALL = '[data-testid="session-filter-all"]'
_LONG_TITLE = (
    "Sehr langer Sessiontitel für das Deployment der Abrechnungsplattform "
    "mit Migrationen, Rollback-Plan und Abstimmung im ganzen Team"
)
_LONG_LINE = (
    "Bitte vor dem Merge noch einmal die Migrationsreihenfolge prüfen, "
    "die Rollback-Schritte dokumentieren und das Monitoring-Dashboard verlinken. "
) * 3


def _shared_root_session(server: TeamChatServer, *, title: str) -> str:
    """A root session Anna owns with READ for Jens; no chat-thread label."""
    created = post_session_bundle(
        httpx.post,
        f"{server.base_url}/v1/sessions",
        bundle_files({"config.yaml": _AGENT_YAML.encode()}),
        metadata={},
        headers=identity_headers(ANNA),
        timeout=30.0,
        trust_env=False,
    )
    created.raise_for_status()
    session_id = str(created.json()["session_id"])
    fetch_bootstrap(server, as_user=JENS)
    _request(
        server,
        "PUT",
        f"/v1/sessions/{session_id}/permissions",
        as_user=ANNA,
        body={"user_id": JENS, "level": _LEVEL_READ},
    )
    _request(server, "PATCH", f"/v1/sessions/{session_id}", as_user=ANNA, body={"title": title})
    return session_id


def _post_line(server: TeamChatServer, session_id: str, *, as_user: str, body: str) -> Any:
    return _request(
        server, "POST", f"/v1/sessions/{session_id}/chat", as_user=as_user, body={"body": body}
    ).json()


def _sidebar_row(page: Page, session_id: str) -> Locator:
    return page.locator(f"{_SIDEBAR} li").filter(has=page.locator(f'a[href="/c/{session_id}"]'))


def _assert_inside(element: Locator, container: Locator, name: str) -> None:
    """*element* fits *container* horizontally and does not scroll inside it."""
    inner, outer = element.bounding_box(), container.bounding_box()
    assert inner is not None and outer is not None, f"{name} has no box"
    assert inner["x"] >= outer["x"] - 0.5, f"{name} starts left of its container"
    assert inner["x"] + inner["width"] <= outer["x"] + outer["width"] + 0.5, (
        f"{name} overflows its container: {inner} vs {outer}"
    )
    scroll_width, client_width = element.evaluate("e => [e.scrollWidth, e.clientWidth]")
    assert scroll_width <= client_width, f"{name} scrolls horizontally"


@_VIEWPORTS
def test_ac_029a_header_toggle_opens_session_chat_with_prior_lines(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Anna opens a shared root session; the header toggle shows the stored lines.

    H-1 / A11Y-03: on the phone the column is a modal dialog with focus inside
    and Escape returns focus to the toggle; on desktop it is not a dialog.
    """
    token = f"t113-{uuid.uuid4().hex[:8]}"
    session_id = _shared_root_session(team_chat_server, title=f"{_LONG_TITLE} {token}")
    _post_line(team_chat_server, session_id, as_user=JENS, body=f"Schaut ihr kurz drauf? {token}")
    _post_line(team_chat_server, session_id, as_user=ANNA, body=f"{_LONG_LINE}{token}")

    page = page_as(ANNA, viewport=viewport)
    page.goto(f"{team_chat_server.base_url}/c/{session_id}")
    toggle = page.get_by_test_id(CHAT_TEST_IDS["sessionColumnToggle"])
    expect(toggle).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    if viewport == PHONE:
        _assert_touch_target(toggle, "session chat toggle")
    toggle.click()

    column = page.get_by_test_id(CHAT_TEST_IDS["sessionColumn"])
    expect(column).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    rows = column.get_by_test_id(CHAT_TEST_IDS["message"])
    expect(rows.filter(has_text=f"Schaut ihr kurz drauf? {token}")).to_have_count(1)
    long_row = rows.filter(has_text=token).filter(has_text="Rollback-Schritte")
    expect(long_row).to_have_count(1)
    expect(column.get_by_test_id(CHAT_TEST_IDS["sessionColumnError"])).to_have_count(0)

    # NFR-032: the long line wraps inside the column, the column fits the
    # viewport, and the page does not scroll sideways.
    _assert_inside(long_row, column, "long session chat line")
    box = column.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
    _assert_no_horizontal_scroll(page)
    if viewport == PHONE:
        _assert_touch_target(
            column.get_by_role("button", name="Session-Chat schließen"), "column close button"
        )
        _assert_touch_target(column.get_by_role("button", name="Senden"), "composer send button")
        _assert_focus_inside_modal_and_escape_returns(page, column, toggle)
    else:
        # Desktop stays a non-modal panel, but focus still moves into it.
        expect(page.get_by_role("dialog")).to_have_count(0)
        _assert_focus_inside(column)


def _assert_focus_inside(container: Locator) -> None:
    """Focus sits on a descendant of *container* (heading or composer); retries."""
    expect(container.locator(":focus")).to_have_count(1)


def _assert_focus_inside_modal_and_escape_returns(
    page: Page, column: Locator, toggle: Locator
) -> None:
    """H-1 / A11Y-03: phone dialog takes focus, Escape closes it, focus goes back."""
    expect(page.get_by_role("dialog")).to_have_count(1)
    expect(column).to_have_attribute("role", "dialog")
    _assert_focus_inside(column)
    page.keyboard.press("Escape")
    expect(column).to_have_count(0)
    expect(toggle).to_be_focused()
    _assert_touch_target(toggle, "session chat toggle after close")


@_VIEWPORTS
def test_ac_085a_grantee_opens_agent_thread_session_from_thread_header(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Jens (READ) sees "Session öffnen" in the Thread and it opens the session."""
    channel = _agent_channel(team_chat_server)
    page = page_as(JENS, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_thread(page, thread.token)

    link = pane.get_by_test_id(CHAT_TEST_IDS["threadOpenSession"])
    expect(link).to_have_text("Session öffnen", timeout=_LOAD_TIMEOUT_MS)
    expect(link).to_have_attribute("href", f"/c/{thread.session_id}")
    _assert_no_horizontal_scroll(page)
    if viewport == PHONE:
        _assert_touch_target(link, "Session öffnen")
    link.click()
    expect(page).to_have_url(f"{team_chat_server.base_url}/c/{thread.session_id}")


def test_ac_085a_grantee_main_sidebar_hides_agent_thread_session(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Under "All sessions" Jens sees a plain shared session but not the Agent-Thread one."""
    channel = _agent_channel(team_chat_server)
    thread = _start_agent_thread(team_chat_server, channel["id"])
    plain_id = _shared_root_session(team_chat_server, title=f"Geteilt {uuid.uuid4().hex[:8]}")

    page = page_as(JENS, viewport=DESKTOP)
    page.goto(f"{team_chat_server.base_url}/c/{plain_id}")
    # The filter button only takes pointer events while its header is hovered.
    session_filter = page.locator(_FILTER)
    expect(session_filter).to_be_attached(timeout=_LOAD_TIMEOUT_MS)
    session_filter.hover()
    session_filter.click()
    page.locator(_FILTER_ALL).click()

    # Positive control: the shared list has loaded with the plain session.
    expect(_sidebar_row(page, plain_id)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(_sidebar_row(page, thread.session_id)).to_have_count(0)


def test_ac_085b_owner_main_sidebar_shows_agent_thread_session_with_glyph(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Anna owns the Agent-Thread session: her sidebar lists it with the thread glyph."""
    channel = _agent_channel(team_chat_server)
    thread = _start_agent_thread(team_chat_server, channel["id"])

    page = page_as(ANNA, viewport=DESKTOP)
    page.goto(f"{team_chat_server.base_url}/c/{thread.session_id}")
    row = _sidebar_row(page, thread.session_id)
    expect(row).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(row.get_by_test_id(CHAT_TEST_IDS["agentThreadGlyph"])).to_be_visible()
    _assert_no_horizontal_scroll(page)
