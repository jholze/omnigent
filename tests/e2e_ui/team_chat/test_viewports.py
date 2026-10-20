"""E2E: the team chat shell fits the desktop and phone viewports.

Each UI change is checked at 1280x800 and 390x844. A channel name at the
server's 40-character limit must not make the page scroll horizontally.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    TeamChatServer,
    seed_channel,
)

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
_MAX_CHANNEL_NAME_LENGTH = 40


def _assert_no_horizontal_scroll(page: Page) -> None:
    scroll_width, client_width = page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert scroll_width <= client_width, (
        f"page scrolls horizontally: scrollWidth={scroll_width} > clientWidth={client_width}"
    )


@pytest.fixture(scope="module")
def long_channel(team_chat_server: TeamChatServer) -> dict[str, Any]:
    """A channel whose name uses all 40 allowed ``[a-z0-9-]`` characters."""
    name = f"vp-{uuid.uuid4().hex}-long"[:_MAX_CHANNEL_NAME_LENGTH]
    assert len(name) == _MAX_CHANNEL_NAME_LENGTH
    return seed_channel(team_chat_server, as_user=ANNA, name=name)


def test_t006_desktop_1280_long_channel_name_does_not_scroll(
    team_chat_server: TeamChatServer, page_as, long_channel: dict[str, Any]
) -> None:
    """At 1280x800 the sidebar shows and a 40-char channel heading fits."""
    page = page_as(ANNA)
    page.set_viewport_size(DESKTOP)
    page.goto(f"{team_chat_server.base_url}/chat")
    sidebar = page.get_by_test_id(CHAT_TEST_IDS["sidebar"])
    expect(sidebar).to_be_visible(timeout=30_000)
    _assert_no_horizontal_scroll(page)

    label = f"#{long_channel['name']}"
    sidebar.get_by_role("link", name=label, exact=True).click()
    expect(page.get_by_role("heading", level=1)).to_have_text(label, timeout=15_000)
    _assert_no_horizontal_scroll(page)


def test_t006_phone_390_channel_shows_back_control(
    team_chat_server: TeamChatServer, page_as, long_channel: dict[str, Any]
) -> None:
    """At 390x844 an open channel has a 44px back control and no sidebar."""
    page = page_as(ANNA)
    page.set_viewport_size(PHONE)
    page.goto(f"{team_chat_server.base_url}/chat/{long_channel['id']}")
    back = page.get_by_test_id(CHAT_TEST_IDS["back"])
    expect(back).to_be_visible(timeout=30_000)
    box = back.bounding_box()
    assert box is not None
    assert box["height"] >= 44, f"back control is {box['height']}px tall"
    _assert_no_horizontal_scroll(page)
    expect(page.get_by_test_id(CHAT_TEST_IDS["sidebar"])).to_be_hidden()

    page.get_by_role("button", name="Open sidebar").click()
    chat_nav = page.get_by_test_id(CHAT_TEST_IDS["navEntry"])
    expect(chat_nav).to_be_visible()
    nav_box = chat_nav.bounding_box()
    assert nav_box is not None
    assert nav_box["height"] >= 44, f"chat nav is {nav_box['height']}px tall"
