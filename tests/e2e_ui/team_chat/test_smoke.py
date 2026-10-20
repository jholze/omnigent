"""E2E smoke: the team chat entry follows the ``team_chat`` flag.

Flag on: Anna (runner owner) and Jens each open their own browser context,
both see the "Chat" entry, and the server tells them apart. Flag off: no
"Chat" entry and the chat API answers 404.
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
