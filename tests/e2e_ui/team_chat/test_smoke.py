"""E2E smoke: the team chat entry follows the ``team_chat`` flag.

Flag on: Anna (runner owner) and Jens each open their own browser context,
both see the "Chat" entry, and the server tells them apart; /chat lists
#general and an empty DM section. Flag off: no "Chat" entry, the chat API
answers 404, and /chat says chat is not enabled.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import Page, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    identity_headers,
)


def _me(server: TeamChatServer, email: str) -> str:
    resp = httpx.get(
        f"{server.base_url}/v1/me", headers=identity_headers(email), timeout=10.0, trust_env=False
    )
    resp.raise_for_status()
    return resp.json()["user_id"]


def _open_app(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/")
    expect(page.get_by_test_id("sidebar-primary-nav")).to_be_visible(timeout=30_000)


def test_t004_ac1_flag_on_two_users_see_chat_entry(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """With ``team_chat`` on, Anna and Jens both see "Chat" as distinct principals."""
    assert team_chat_server.runner_owner == ANNA
    assert _me(team_chat_server, ANNA) != _me(team_chat_server, JENS)

    for email in (ANNA, JENS):
        page = page_as(email)
        _open_app(page, team_chat_server.base_url)
        entry = page.get_by_test_id(CHAT_TEST_IDS["navEntry"])
        expect(entry).to_be_visible(timeout=15_000)
        entry.click()
        expect(page.get_by_test_id(CHAT_TEST_IDS["page"])).to_be_visible(timeout=15_000)


def test_t004_ac2_flag_off_hides_chat_entry_and_api(
    team_chat_server_flag_off: TeamChatServer, page_as
) -> None:
    """Without the flag there is no "Chat" entry and ``/v1/chat/bootstrap`` is 404."""
    page = page_as(ANNA)
    _open_app(page, team_chat_server_flag_off.base_url)
    expect(page.get_by_test_id(CHAT_TEST_IDS["navEntry"])).to_have_count(0)
    nav = page.get_by_test_id("sidebar-primary-nav")
    expect(nav.get_by_role("link", name="Chat", exact=True)).to_have_count(0)

    resp = httpx.get(
        f"{team_chat_server_flag_off.base_url}/v1/chat/bootstrap",
        headers=identity_headers(ANNA),
        timeout=10.0,
        trust_env=False,
    )
    assert resp.status_code == 404


def test_ac_001a_flag_on_chat_lists_general_and_empty_dms(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Flag on: Anna's /chat shows #general under Channels and no direct messages."""
    page = page_as(ANNA)
    page.goto(f"{team_chat_server.base_url}/chat")
    sidebar = page.get_by_test_id(CHAT_TEST_IDS["sidebar"])
    channels = sidebar.get_by_role("region", name="Channels", exact=True)
    expect(channels.get_by_role("link", name="#general", exact=True)).to_be_visible(timeout=30_000)
    dms = sidebar.get_by_role("region", name="Direktnachrichten", exact=True)
    expect(dms).to_be_visible()
    expect(dms.get_by_role("heading", name="Direktnachrichten")).to_be_visible()
    expect(dms.get_by_role("listitem")).to_have_count(0)


def test_ac_001b_flag_off_chat_route_says_not_enabled(
    team_chat_server_flag_off: TeamChatServer, page_as
) -> None:
    """Flag off: /chat says chat is not enabled and the main sidebar has no Chat entry."""
    page = page_as(ANNA)
    page.goto(f"{team_chat_server_flag_off.base_url}/chat")
    expect(
        page.get_by_text("Chat ist auf diesem Server nicht aktiviert", exact=True)
    ).to_be_visible(timeout=30_000)
    nav = page.get_by_test_id("sidebar-primary-nav")
    expect(nav).to_be_visible()
    expect(page.get_by_test_id(CHAT_TEST_IDS["navEntry"])).to_have_count(0)
    expect(nav.get_by_role("link", name="Chat", exact=True)).to_have_count(0)
