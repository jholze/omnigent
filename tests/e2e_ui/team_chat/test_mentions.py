"""E2E: mention autocomplete in the team chat composer (T-110, FR-026).

Anna types ``@`` in the channel composer and in the thread composer at
1280x800 and 390x844. The stored body is read from the messages API, so the
assertion covers the posted ``<@kind:id>`` token, not the rendered chip.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import pytest
from playwright.sync_api import Locator, Page, Request, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    fetch_bootstrap,
    join_channel,
    list_messages,
    seed_channel,
    seed_message,
)

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
JENS_TOKEN = f"<@user:{JENS}>"
AGENT_TOKEN = "<@omnigent_agent:agent>"
_MIN_TOUCH_TARGET_PX = 44
_LIST_BUDGET_MS = 100
_MESSAGES_URL = re.compile(r"/v1/chat/channels/[^/]+/messages(\?.*)?$")
_LOAD_TIMEOUT_MS = 30_000
_STORE_TIMEOUT_S = 15.0

# Records the time from the first keydown to the list entering the DOM.
_ARM_LATENCY_PROBE = """(testId) => {
  window.__mentionListMs = null;
  let pressedAt = null;
  const onKey = () => { if (pressedAt === null) pressedAt = performance.now(); };
  document.addEventListener("keydown", onKey, { capture: true });
  const observer = new MutationObserver(() => {
    if (pressedAt === null) return;
    if (document.querySelector(`[data-testid="${testId}"]`)) {
      window.__mentionListMs = performance.now() - pressedAt;
      observer.disconnect();
      document.removeEventListener("keydown", onKey, { capture: true });
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}"""


@pytest.fixture(scope="module")
def mention_channel(team_chat_server: TeamChatServer) -> dict[str, Any]:
    """A channel Anna created and Jens joined, with one root message."""
    channel = seed_channel(team_chat_server, as_user=ANNA)
    fetch_bootstrap(team_chat_server, as_user=JENS)
    join_channel(team_chat_server, as_user=JENS, channel_id=channel["id"])
    root = seed_message(
        team_chat_server, as_user=ANNA, channel_id=channel["id"], body="Thread für Erwähnungen"
    )
    return {"channel": channel, "root": root}


def _channel_path(setup: dict[str, Any]) -> str:
    return f"/chat/{quote(setup['channel']['id'], safe='')}"


def _thread_path(setup: dict[str, Any]) -> str:
    return f"{_channel_path(setup)}/t/{quote(setup['root']['id'], safe='')}"


def _composer_scope(page: Page, *, thread: bool) -> Page | Locator:
    """The thread pane; on a channel path with no thread open, the page."""
    return page.get_by_test_id(CHAT_TEST_IDS["threadPane"]) if thread else page


def _open_composer(page: Page, server: TeamChatServer, path: str, *, thread: bool) -> Locator:
    page.goto(f"{server.base_url}{path}")
    scope = _composer_scope(page, thread=thread)
    composer_input = scope.get_by_test_id(CHAT_TEST_IDS["composerInput"])
    expect(composer_input).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    composer_input.click()
    return composer_input


def _record_posts(page: Page) -> list[Request]:
    posts: list[Request] = []

    def _on_request(request: Request) -> None:
        if request.method == "POST" and _MESSAGES_URL.search(request.url):
            posts.append(request)

    page.on("request", _on_request)
    return posts


def _type_at_and_measure(page: Page, composer_input: Locator) -> float:
    """Press ``@`` and return the ms until the mention list is in the DOM."""
    page.evaluate(_ARM_LATENCY_PROBE, CHAT_TEST_IDS["mentionList"])
    composer_input.press("@")
    expect(page.get_by_test_id(CHAT_TEST_IDS["mentionList"])).to_be_visible()
    elapsed = page.evaluate("() => window.__mentionListMs")
    assert isinstance(elapsed, (int, float)), "mention list never entered the DOM"
    return float(elapsed)


def _chip_tokens(page: Page) -> list[str | None]:
    chips = page.get_by_test_id(CHAT_TEST_IDS["composerMentionChip"])
    return [chips.nth(index).get_attribute("data-mention-token") for index in range(chips.count())]


def _stored_body(
    server: TeamChatServer,
    setup: dict[str, Any],
    marker: str,
    *,
    thread: bool,
) -> str:
    """Poll the messages API until the message carrying *marker* is stored."""
    root_id = setup["root"]["id"] if thread else None
    deadline = time.monotonic() + _STORE_TIMEOUT_S
    while time.monotonic() < deadline:
        items = list_messages(
            server, as_user=ANNA, channel_id=setup["channel"]["id"], thread_root_id=root_id
        )
        for item in items:
            if marker in item["body"]:
                return item["body"]
        time.sleep(0.1)
    raise AssertionError(f"no stored message contains {marker!r}")


def _marker() -> str:
    return f"m-{uuid.uuid4().hex[:8]}"


def _assert_row_heights(options: Locator) -> None:
    for index in range(options.count()):
        box = options.nth(index).bounding_box()
        assert box is not None, f"option {index} has no box"
        assert box["height"] >= _MIN_TOUCH_TARGET_PX, f"option {index} is {box['height']}px tall"


def _assert_inside_viewport(page: Page, element: Locator) -> None:
    box = element.bounding_box()
    assert box is not None, "mention list has no box"
    width, height = page.evaluate(
        "() => [document.documentElement.clientWidth, document.documentElement.clientHeight]"
    )
    assert box["x"] >= 0 and box["y"] >= 0, f"mention list starts off-screen: {box}"
    assert box["x"] + box["width"] <= width, f"mention list overflows {width}px: {box}"
    assert box["y"] + box["height"] <= height, f"mention list overflows {height}px: {box}"


@pytest.mark.parametrize("thread", [False, True], ids=["channel", "thread"])
def test_ac_026c_ac_026f_ac_026g_desktop_keyboard_picks_and_sends_tokens(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    mention_channel: dict[str, Any],
    thread: bool,
) -> None:
    """1280x800: @ opens within 100 ms; ArrowDown+Enter picks; Enter then sends tokens."""
    page = page_as(ANNA, viewport=DESKTOP)
    path = _thread_path(mention_channel) if thread else _channel_path(mention_channel)
    composer_input = _open_composer(page, team_chat_server, path, thread=thread)
    posts = _record_posts(page)
    marker = _marker()
    composer_input.press_sequentially(f"{marker} ")

    elapsed = _type_at_and_measure(page, composer_input)
    assert elapsed <= _LIST_BUDGET_MS, f"mention list took {elapsed:.1f} ms"

    composer_input.press_sequentially("je")
    options = page.get_by_test_id(CHAT_TEST_IDS["mentionOption"])
    expect(options).to_have_count(1)
    expect(options.first).to_have_attribute("data-mention-id", JENS)
    composer_input.press("ArrowDown")
    composer_input.press("Enter")
    expect(page.get_by_test_id(CHAT_TEST_IDS["mentionList"])).to_have_count(0)
    assert _chip_tokens(page) == [JENS_TOKEN]
    assert posts == [], "Enter on the open list must not send"

    composer_input.press_sequentially("frag @ag")
    expect(options.first).to_have_attribute("data-mention-kind", "omnigent_agent")
    composer_input.press("Enter")
    assert _chip_tokens(page) == [JENS_TOKEN, AGENT_TOKEN]
    assert posts == [], "Enter on the open list must not send"

    composer_input.press("Enter")
    body = _stored_body(team_chat_server, mention_channel, marker, thread=thread)
    assert body == f"{marker} {JENS_TOKEN} frag {AGENT_TOKEN}"


def test_ac_026e_ac_026f_desktop_empty_list_and_escape_insert_nothing(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    mention_channel: dict[str, Any],
) -> None:
    """1280x800: @zz shows Niemand gefunden; Escape closes it; no chip, no send."""
    page = page_as(ANNA, viewport=DESKTOP)
    composer_input = _open_composer(
        page, team_chat_server, _channel_path(mention_channel), thread=False
    )
    posts = _record_posts(page)

    composer_input.press_sequentially("@zz")
    empty = page.get_by_test_id(CHAT_TEST_IDS["mentionEmpty"])
    expect(empty).to_have_text("Niemand gefunden")
    expect(page.get_by_test_id(CHAT_TEST_IDS["mentionOption"])).to_have_count(0)

    composer_input.press("Escape")
    expect(page.get_by_test_id(CHAT_TEST_IDS["mentionList"])).to_have_count(0)
    expect(composer_input).to_have_value("@zz")
    assert _chip_tokens(page) == []
    assert posts == []


@pytest.mark.parametrize("thread", [False, True], ids=["channel", "thread"])
def test_ac_026c_ac_026g_phone_tap_picks_and_send_posts_token(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    mention_channel: dict[str, Any],
    thread: bool,
) -> None:
    """390x844: rows are 44 px and on screen; a tap picks; Senden posts the token."""
    page = page_as(ANNA, viewport=PHONE)
    path = _thread_path(mention_channel) if thread else _channel_path(mention_channel)
    composer_input = _open_composer(page, team_chat_server, path, thread=thread)
    marker = _marker()
    composer_input.press_sequentially(f"{marker} ")

    elapsed = _type_at_and_measure(page, composer_input)
    assert elapsed <= _LIST_BUDGET_MS, f"mention list took {elapsed:.1f} ms"
    mention_list = page.get_by_test_id(CHAT_TEST_IDS["mentionList"])
    options = page.get_by_test_id(CHAT_TEST_IDS["mentionOption"])
    expect(options.first).to_be_visible()
    _assert_row_heights(options)
    _assert_inside_viewport(page, mention_list)

    composer_input.press_sequentially("je")
    jens = page.locator(
        f'[data-testid="{CHAT_TEST_IDS["mentionOption"]}"][data-mention-id="{JENS}"]'
    )
    expect(jens).to_have_count(1)
    jens.click()
    expect(mention_list).to_have_count(0)
    assert _chip_tokens(page) == [JENS_TOKEN]

    _composer_scope(page, thread=thread).get_by_test_id(CHAT_TEST_IDS["composer"]).get_by_role(
        "button", name="Senden"
    ).click()
    body = _stored_body(team_chat_server, mention_channel, marker, thread=thread)
    assert body == f"{marker} {JENS_TOKEN}"
