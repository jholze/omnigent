"""E2E: the unread badge follows the reader across browser contexts (AC-009a).

Anna has ``/chat`` open in two contexts. Jens posted twice in #billing, so
both sidebars show "2". Opening #billing in context A posts Anna's read
cursor; the server's ``read.updated`` frame clears context B's badge without
a reload. Checked at 1280x800 and 390x844 (NFR-032).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

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
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
)

_UPDATES_SOCKET_PATH = "/v1/sessions/updates"
_LOAD_TIMEOUT_MS = 30_000
_CROSS_CONTEXT_BUDGET_MS = 1_000
_BOOTSTRAP_PATH = "/v1/chat/bootstrap"
_SCREENSHOT_DIR = Path("/tmp/omnigent-chat-ux/dm-bootstrap")


@pytest.fixture
def billing_unread(team_chat_server: TeamChatServer) -> dict[str, Any]:
    """A fresh #billing-… channel by Anna with two messages from Jens she has not read."""
    channel = seed_channel(team_chat_server, as_user=ANNA, name=f"billing-{uuid.uuid4().hex[:6]}")
    fetch_bootstrap(team_chat_server, as_user=JENS)
    join_channel(team_chat_server, as_user=JENS, channel_id=channel["id"])
    for body in ("Rechnungslauf ist durch", "Bitte einmal gegenlesen"):
        seed_message(team_chat_server, as_user=JENS, channel_id=channel["id"], body=body)
    return channel


def _open_chat_home(page: Page, server: TeamChatServer) -> None:
    """Load ``/chat`` and wait for the updates socket that carries read frames."""
    with page.expect_websocket(
        lambda socket: _UPDATES_SOCKET_PATH in socket.url, timeout=_LOAD_TIMEOUT_MS
    ):
        page.goto(f"{server.base_url}/chat")
    expect(page.get_by_test_id(CHAT_TEST_IDS["sidebar"])).to_be_visible(timeout=_LOAD_TIMEOUT_MS)


def _billing_entry(page: Page, channel_id: str) -> Locator:
    return page.get_by_test_id(CHAT_TEST_IDS["sidebar"]).locator(
        f'a[href="/chat/{quote(channel_id, safe="")}"]'
    )


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
def test_ac_009a_opening_billing_in_one_context_clears_badge_in_other_within_1s(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    billing_unread: dict[str, Any],
    viewport: dict[str, int],
) -> None:
    """Both contexts show "2" on #billing; opening it in A clears B's badge in 1 s."""
    context_a = page_as(ANNA, viewport=viewport)
    context_b = page_as(ANNA, viewport=viewport)
    for page in (context_a, context_b):
        _open_chat_home(page, team_chat_server)
        entry = _billing_entry(page, billing_unread["id"])
        expect(entry.get_by_test_id(CHAT_TEST_IDS["unreadBadge"])).to_have_text(
            "2", timeout=_LOAD_TIMEOUT_MS
        )
        if viewport == PHONE:
            _assert_touch_target(entry, "#billing sidebar entry")
        _assert_no_horizontal_scroll(page)

    _billing_entry(context_a, billing_unread["id"]).click()
    expect(
        _billing_entry(context_b, billing_unread["id"]).get_by_test_id(
            CHAT_TEST_IDS["unreadBadge"]
        )
    ).to_have_count(0, timeout=_CROSS_CONTEXT_BUDGET_MS)
    expect(context_a.get_by_test_id(CHAT_TEST_IDS["messageList"]).first).to_be_visible()
    _assert_no_horizontal_scroll(context_b)


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
def test_live_post_and_mention_raise_sidebar_badges_without_bootstrap_refetch(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
) -> None:
    """Jens posts, then mentions Anna: badges read "1", then "@1" and "2"; no bootstrap GET."""
    channel = seed_channel(team_chat_server, as_user=ANNA, name=f"billing-{uuid.uuid4().hex[:6]}")
    fetch_bootstrap(team_chat_server, as_user=JENS)
    join_channel(team_chat_server, as_user=JENS, channel_id=channel["id"])

    page = page_as(ANNA, viewport=viewport)
    _open_chat_home(page, team_chat_server)
    entry = _billing_entry(page, channel["id"])
    unread_badge = entry.get_by_test_id(CHAT_TEST_IDS["unreadBadge"])
    mention_badge = entry.get_by_test_id(CHAT_TEST_IDS["mentionBadge"])
    expect(entry).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(unread_badge).to_have_count(0)

    bootstrap_gets: list[str] = []
    page.on(
        "request",
        lambda request: (
            bootstrap_gets.append(request.url)
            if request.method == "GET" and urlparse(request.url).path == _BOOTSTRAP_PATH
            else None
        ),
    )

    seed_message(
        team_chat_server, as_user=JENS, channel_id=channel["id"], body="Rechnungslauf ist durch"
    )
    expect(unread_badge).to_have_text("1", timeout=_LOAD_TIMEOUT_MS)
    assert bootstrap_gets == []

    seed_message(
        team_chat_server,
        as_user=JENS,
        channel_id=channel["id"],
        body=f"<@user:{ANNA}> bitte einmal gegenlesen",
    )
    expect(mention_badge).to_have_text("@1", timeout=_LOAD_TIMEOUT_MS)
    expect(unread_badge).to_have_text("2")
    assert bootstrap_gets == []
    assert page.url.rstrip("/").endswith("/chat")

    if viewport == PHONE:
        _assert_touch_target(entry, "#billing sidebar entry")
    _assert_no_horizontal_scroll(page)
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(_SCREENSHOT_DIR / f"sidebar-mention-{viewport['width']}x{viewport['height']}.png")
    )
