"""E2E: axe-core finds no serious or critical violations in team chat (NFR-009).

Scans three views with ``axe-playwright-python``: a channel under ``/chat``,
the same channel with its thread pane open, and the Settings section
"Agenten". Each view runs at 1280x800 and 390x844 (NFR-032). Failure output
names the rule, its impact and the CSS targets, never page text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

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
from tests.e2e_ui.team_chat.test_viewports import DESKTOP, PHONE, _assert_no_horizontal_scroll

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_BLOCKING_IMPACTS = frozenset({"serious", "critical"})
_LOAD_TIMEOUT_MS = 30_000
_AXE = Axe()


@pytest.fixture(scope="module")
def a11y_conversation(team_chat_server: TeamChatServer) -> dict[str, Any]:
    """A channel with messages from Anna and Jens and one thread reply."""
    channel = seed_channel(team_chat_server, as_user=ANNA)
    fetch_bootstrap(team_chat_server, as_user=JENS)
    join_channel(team_chat_server, as_user=JENS, channel_id=channel["id"])
    root = seed_message(
        team_chat_server, as_user=ANNA, channel_id=channel["id"], body="Release heute Abend?"
    )
    seed_message(
        team_chat_server,
        as_user=JENS,
        channel_id=channel["id"],
        body="Passt",
        thread_root_id=root["id"],
    )
    seed_message(
        team_chat_server,
        as_user=JENS,
        channel_id=channel["id"],
        body=f"<@user:{ANNA}> schau mal",
    )
    return {"channel": channel, "root": root}


def _settle_animations(page: Page) -> None:
    """Wait for finite enter animations, so contrast is measured at full opacity."""
    page.evaluate(
        """() => Promise.all(document.getAnimations()
            .filter((a) => a.effect?.getComputedTiming?.().iterations !== Infinity)
            .map((a) => a.finished.catch(() => null)))"""
    )


def _assert_no_blocking_violations(page: Page, view: str) -> None:
    """axe reports no serious or critical violation on the current page."""
    _settle_animations(page)
    response = _AXE.run(page).response
    blocking = [
        {
            "rule": violation["id"],
            "impact": violation["impact"],
            "targets": [node["target"] for node in violation["nodes"]][:5],
        }
        for violation in response["violations"]
        if violation.get("impact") in _BLOCKING_IMPACTS
    ]
    assert not blocking, f"{view}: serious/critical axe violations: {blocking}"


def _channel_path(conversation: dict[str, Any]) -> str:
    return f"/chat/{quote(conversation['channel']['id'], safe='')}"


@_VIEWPORTS
def test_nfr_009_chat_channel_has_no_serious_or_critical_axe_violations(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    a11y_conversation: dict[str, Any],
    viewport: dict[str, int],
) -> None:
    """The open channel under /chat: messages, mention chip, composer, sidebar."""
    page = page_as(ANNA, viewport=viewport)
    page.goto(f"{team_chat_server.base_url}{_channel_path(a11y_conversation)}")
    expect(
        page.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="schau mal")
    ).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(page.get_by_test_id(CHAT_TEST_IDS["composerInput"])).to_be_visible()
    _assert_no_horizontal_scroll(page)
    _assert_no_blocking_violations(page, "/chat channel")


@_VIEWPORTS
def test_nfr_009_thread_pane_has_no_serious_or_critical_axe_violations(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    a11y_conversation: dict[str, Any],
    viewport: dict[str, int],
) -> None:
    """The channel with its thread pane open, reply and thread composer shown."""
    page = page_as(ANNA, viewport=viewport)
    root_id = quote(a11y_conversation["root"]["id"], safe="")
    page.goto(f"{team_chat_server.base_url}{_channel_path(a11y_conversation)}/t/{root_id}")
    pane = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(pane.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="Passt")).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )
    expect(pane.get_by_test_id(CHAT_TEST_IDS["composerInput"])).to_be_visible()
    _assert_no_horizontal_scroll(page)
    _assert_no_blocking_violations(page, "thread pane")


@_VIEWPORTS
def test_nfr_009_settings_agent_accounts_has_no_serious_or_critical_axe_violations(
    team_chat_server: TeamChatServer, page_as: Callable[..., Page], viewport: dict[str, int]
) -> None:
    """Settings, section "Agenten" (team chat's settings surface)."""
    page = page_as(ANNA, viewport=viewport)
    page.goto(f"{team_chat_server.base_url}/settings/agent-accounts")
    expect(page.get_by_test_id("agent-accounts-section")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    _assert_no_horizontal_scroll(page)
    _assert_no_blocking_violations(page, "settings agent accounts")
