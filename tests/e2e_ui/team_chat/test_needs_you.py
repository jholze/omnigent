"""E2E: the "Braucht dich" section of the inbox (T-112, FR-010, NFR-032).

Anna's agent thread waits on a real parked approval (the session's
permission-request hook) and Jens mentions Anna in the channel. The inbox
lists both rows; each links to its conversation. The section passes the
viewport gate at 1280x800 and 390x844.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

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
    AgentThread,
    _parked_approval,
    _start_agent_thread,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
    _assert_within_viewport,
)

_LOAD_TIMEOUT_MS = 30_000
_NEEDS_YOU_URL = re.compile(r"/v1/chat/needs-you$")
_MAX_CHANNEL_NAME_LENGTH = 40
# Header-auth label is the capitalised email local part, far wider than a phone.
_LONG_NAME_USER = "sehr.langer.vorname.und.noch.ein.langer.nachname.fuer.umbruch@team-chat.test"
_LONG_NAME_LABEL = "Sehr.langer.vorname.und.noch.ein.langer.nachname.fuer.umbruch"


def _mention_body() -> str:
    return f"<@user:{ANNA}> schau mal"


def _members(server: TeamChatServer, channel_id: str, *users: str) -> None:
    for user in users:
        fetch_bootstrap(server, as_user=user)
        join_channel(server, as_user=user, channel_id=channel_id)


def _chat_path(channel_id: str, root_id: str | None = None) -> str:
    path = f"/chat/{quote(channel_id, safe='')}"
    return f"{path}/t/{quote(root_id, safe='')}" if root_id else path


def _item(page: Page, kind: str, path: str) -> Locator:
    return page.locator(f'[data-testid="needs-you-item"][data-kind="{kind}"]').filter(
        has=page.locator(f'a[href="{path}"]')
    )


def _open_inbox(page: Page, server: TeamChatServer) -> Locator:
    page.goto(f"{server.base_url}/inbox")
    view = page.get_by_test_id("needs-you-view")
    expect(view).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    return view


@pytest.fixture
def billing_waiting(team_chat_server: TeamChatServer) -> Iterator[dict[str, Any]]:
    """#billing: Anna's agent thread waits on an approval; Jens mentions Anna."""
    channel = seed_channel(team_chat_server, as_user=ANNA, name="billing")
    _members(team_chat_server, channel["id"], JENS, "bob@team-chat.test")
    thread: AgentThread = _start_agent_thread(team_chat_server, channel["id"])
    with _parked_approval(team_chat_server, thread.session_id):
        mention = seed_message(
            team_chat_server, as_user=JENS, channel_id=channel["id"], body=_mention_body()
        )
        yield {"channel": channel, "thread": thread, "mention": mention}


def test_ac_010a_braucht_dich_lists_mention_and_approval_and_opens_each(
    team_chat_server: TeamChatServer, page_as, billing_waiting: dict[str, Any]
) -> None:
    """Anna sees Jens's mention and her waiting approval; each row opens its place."""
    channel_id = billing_waiting["channel"]["id"]
    thread: AgentThread = billing_waiting["thread"]
    mention_path = _chat_path(channel_id)
    approval_path = _chat_path(channel_id, thread.root_id)
    page = page_as(ANNA, viewport=DESKTOP)

    _open_inbox(page, team_chat_server)
    mention = _item(page, "mention", mention_path)
    approval = _item(page, "approval", approval_path)
    expect(mention).to_contain_text("Jens hat dich erwähnt", timeout=_LOAD_TIMEOUT_MS)
    expect(mention).to_contain_text("#billing")
    expect(approval).to_contain_text("Agent braucht eine Antwort")
    expect(approval).to_contain_text("#billing")

    mention.get_by_role("link").click()
    expect(page).to_have_url(f"{team_chat_server.base_url}{mention_path}")
    expect(
        page.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="schau mal")
    ).to_be_visible(timeout=_LOAD_TIMEOUT_MS)

    _open_inbox(page, team_chat_server)
    _item(page, "approval", approval_path).get_by_role("link").click()
    expect(page).to_have_url(f"{team_chat_server.base_url}{approval_path}")
    pane = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(
        pane.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text=thread.token)
    ).to_be_visible(timeout=_LOAD_TIMEOUT_MS)


@pytest.fixture
def long_name_waiting(team_chat_server: TeamChatServer) -> Iterator[dict[str, Any]]:
    """A 40-char channel: a long-named user mentions Anna; Anna's agent thread waits."""
    name = f"ny-{uuid.uuid4().hex}-long"[:_MAX_CHANNEL_NAME_LENGTH]
    channel = seed_channel(team_chat_server, as_user=ANNA, name=name)
    _members(team_chat_server, channel["id"], JENS, "bob@team-chat.test", _LONG_NAME_USER)
    thread = _start_agent_thread(team_chat_server, channel["id"])
    with _parked_approval(team_chat_server, thread.session_id):
        seed_message(
            team_chat_server,
            as_user=_LONG_NAME_USER,
            channel_id=channel["id"],
            body=_mention_body(),
        )
        yield {"channel": channel, "thread": thread}


def _assert_needs_you_gate(page: Page, server: TeamChatServer, waiting: dict[str, Any]) -> None:
    """Every row is a 44px target, the long author name wraps, the page does not scroll."""
    channel_id = waiting["channel"]["id"]
    _open_inbox(page, server)
    long_row = _item(page, "mention", _chat_path(channel_id))
    expect(long_row).to_contain_text(_LONG_NAME_LABEL, timeout=_LOAD_TIMEOUT_MS)
    expect(
        _item(page, "approval", _chat_path(channel_id, waiting["thread"].root_id))
    ).to_be_visible()
    items = page.get_by_test_id("needs-you-item")
    for index in range(items.count()):
        _assert_touch_target(items.nth(index).get_by_role("link"), f"needs-you row {index}")
    _assert_within_viewport(page, long_row.get_by_text(_LONG_NAME_LABEL), "long author name")
    _assert_no_horizontal_scroll(page)


def test_ac_010a_nfr032_desktop_1280_braucht_dich_fits(
    team_chat_server: TeamChatServer, page_as, long_name_waiting: dict[str, Any]
) -> None:
    """At 1280x800 the rows are 44px, a long name wraps, no horizontal scroll."""
    page = page_as(ANNA, viewport=DESKTOP)
    _assert_needs_you_gate(page, team_chat_server, long_name_waiting)


def test_ac_010a_nfr032_phone_390_braucht_dich_fits(
    team_chat_server: TeamChatServer, page_as, long_name_waiting: dict[str, Any]
) -> None:
    """At 390x844 the rows are 44px, a long name wraps, no horizontal scroll."""
    page = page_as(ANNA, viewport=PHONE)
    _assert_needs_you_gate(page, team_chat_server, long_name_waiting)


def test_ac_010a_nfr032_phone_390_braucht_dich_retry_is_44px(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """At 390x844 a failed load shows a 44px "Erneut versuchen" and no horizontal scroll."""
    page = page_as(ANNA, viewport=PHONE)
    page.route(
        _NEEDS_YOU_URL,
        lambda route: route.fulfill(status=503, content_type="application/json", body="{}"),
    )
    _open_inbox(page, team_chat_server)
    retry = page.get_by_test_id("needs-you-retry")
    expect(retry).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    box = retry.bounding_box()
    assert box is not None
    assert box["height"] >= 44, f"retry is {box['height']}px tall"
    _assert_no_horizontal_scroll(page)
