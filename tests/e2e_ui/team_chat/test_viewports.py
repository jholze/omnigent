"""E2E: the team chat shell fits the desktop and phone viewports.

Each UI change is checked at 1280x800 and 390x844. A channel name at the
server's 40-character limit must not make the page scroll horizontally.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import quote

import pytest
from playwright.sync_api import Locator, Page, Route, expect

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

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
_MAX_CHANNEL_NAME_LENGTH = 40
_MIN_TOUCH_TARGET_PX = 44
# Header-auth principal ids are the email; this one is far wider than a phone.
_LONG_NAME_USER = "sehr.langer.vorname.und.noch.ein.langer.nachname.fuer.umbruch@team-chat.test"
_LONG_WORD = "Donaudampfschifffahrtselektrizitaetenhauptbetriebswerkbauunterbeamtengesellschaft"
_MESSAGES_URL = re.compile(r"/v1/chat/channels/[^/]+/messages(\?.*)?$")


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


def _assert_touch_target(control: Locator, name: str) -> None:
    """A phone control is at least 44px tall and wide."""
    expect(control).to_be_visible()
    box = control.bounding_box()
    assert box is not None, f"{name} has no box"
    assert box["height"] >= _MIN_TOUCH_TARGET_PX, f"{name} is {box['height']}px tall"
    assert box["width"] >= _MIN_TOUCH_TARGET_PX, f"{name} is {box['width']}px wide"


def _assert_within_viewport(page: Page, element: Locator, name: str) -> None:
    """The element's right edge stays inside the viewport, so long text wrapped."""
    box = element.bounding_box()
    assert box is not None, f"{name} has no box"
    client_width = page.evaluate("() => document.documentElement.clientWidth")
    assert box["x"] + box["width"] <= client_width, (
        f"{name} ends at {box['x'] + box['width']}px, past the {client_width}px viewport"
    )


@pytest.fixture(scope="module")
def conversation(team_chat_server: TeamChatServer) -> dict[str, Any]:
    """A channel with a long-named author, an unbroken long word, and a thread reply."""
    channel = seed_channel(team_chat_server, as_user=ANNA)
    for user in (JENS, _LONG_NAME_USER):
        # Bootstrap adds the user to #general, so the new-DM dialog lists them.
        fetch_bootstrap(team_chat_server, as_user=user)
        join_channel(team_chat_server, as_user=user, channel_id=channel["id"])
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
        as_user=_LONG_NAME_USER,
        channel_id=channel["id"],
        body=f"{_LONG_WORD} https://example.com/{_LONG_WORD}",
    )
    return {"channel": channel, "root": root}


def _channel_path(conversation: dict[str, Any]) -> str:
    return f"/chat/{quote(conversation['channel']['id'], safe='')}"


def _thread_path(conversation: dict[str, Any]) -> str:
    return f"{_channel_path(conversation)}/t/{quote(conversation['root']['id'], safe='')}"


def _long_author_label(page: Page) -> Locator:
    row = page.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text=_LONG_WORD)
    expect(row).to_be_visible(timeout=30_000)
    return row.get_by_text(_LONG_NAME_USER, exact=True)


def test_nfr032_desktop_1280_channel_with_thread_does_not_scroll(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 1280x800 channel and thread sit side by side; long names and words wrap."""
    page = page_as(ANNA, viewport=DESKTOP)
    page.goto(f"{team_chat_server.base_url}{_thread_path(conversation)}")
    thread = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(thread.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="Passt")).to_be_visible(
        timeout=30_000
    )
    expect(page.get_by_test_id(CHAT_TEST_IDS["sidebar"])).to_be_visible()
    author = _long_author_label(page)
    _assert_no_horizontal_scroll(page)
    _assert_within_viewport(page, author, "long author name")


def test_nfr032_phone_390_channel_controls_are_44px(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 the send button and the reply action are 44px; long names wrap."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_channel_path(conversation)}")
    author = _long_author_label(page)
    _assert_no_horizontal_scroll(page)
    _assert_within_viewport(page, author, "long author name")

    composer = page.get_by_test_id(CHAT_TEST_IDS["composer"])
    _assert_touch_target(composer.get_by_role("button", name="Senden"), "send button")
    _assert_touch_target(page.get_by_test_id(CHAT_TEST_IDS["replyAction"]).first, "reply action")


def test_nfr032_phone_390_thread_replaces_channel_with_44px_controls(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 the thread fills the screen; its back link and send button are 44px."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_thread_path(conversation)}")
    thread = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(thread.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="Passt")).to_be_visible(
        timeout=30_000
    )
    expect(page.get_by_test_id(CHAT_TEST_IDS["back"])).to_be_hidden()
    _assert_no_horizontal_scroll(page)

    back = thread.get_by_test_id(CHAT_TEST_IDS["threadBack"])
    expect(back).to_be_visible()
    back_box = back.bounding_box()
    assert back_box is not None
    assert back_box["height"] >= _MIN_TOUCH_TARGET_PX, f"thread back is {back_box['height']}px"
    _assert_touch_target(
        thread.get_by_test_id(CHAT_TEST_IDS["composer"]).get_by_role("button", name="Senden"),
        "thread send button",
    )


def test_nfr032_phone_390_new_dm_controls_are_44px(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 the new-DM button and each person row are 44px; long names wrap."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}/chat")
    new_dm = page.get_by_test_id(CHAT_TEST_IDS["newDm"])
    expect(new_dm).to_be_visible(timeout=30_000)
    _assert_touch_target(new_dm, "new DM button")

    new_dm.click()
    dialog = page.get_by_test_id(CHAT_TEST_IDS["newDmDialog"])
    expect(dialog).to_be_visible()
    people = dialog.get_by_test_id(CHAT_TEST_IDS["newDmPerson"])
    expect(people.first).to_be_visible()
    for index in range(people.count()):
        person_box = people.nth(index).bounding_box()
        assert person_box is not None
        assert person_box["height"] >= _MIN_TOUCH_TARGET_PX, (
            f"person row {index} is {person_box['height']}px tall"
        )
    long_person = people.filter(has_text=_LONG_NAME_USER)
    _assert_within_viewport(page, long_person, "long person name")
    _assert_no_horizontal_scroll(page)


def test_nfr032_phone_390_failed_send_retry_is_44px(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 a failed send's "erneut senden" control is 44px tall."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_channel_path(conversation)}")
    composer_input = page.get_by_test_id(CHAT_TEST_IDS["composerInput"])
    expect(composer_input).to_be_visible(timeout=30_000)

    def _fail_posts(route: Route) -> None:
        if route.request.method == "POST":
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route(_MESSAGES_URL, _fail_posts)
    composer_input.fill("Telefon offline")
    # Phones send with the button only; Enter inserts a newline.
    page.get_by_test_id(CHAT_TEST_IDS["composer"]).get_by_role("button", name="Senden").click()

    retry = page.get_by_test_id(CHAT_TEST_IDS["sendRetry"])
    expect(retry).to_be_visible(timeout=15_000)
    retry_box = retry.bounding_box()
    assert retry_box is not None
    assert retry_box["height"] >= _MIN_TOUCH_TARGET_PX, f"retry is {retry_box['height']}px tall"
    _assert_no_horizontal_scroll(page)


def _assert_clear_of_open_sidebar(page: Page, control: Locator, name: str) -> None:
    """The control is 44px tall and its box does not overlap any "Open sidebar" button."""
    expect(control).to_be_visible()
    box = control.bounding_box()
    assert box is not None, f"{name} has no box"
    assert box["height"] >= _MIN_TOUCH_TARGET_PX, f"{name} is {box['height']}px tall"
    open_sidebar = page.get_by_role("button", name="Open sidebar", exact=True)
    expect(open_sidebar.first).to_be_visible()
    for index in range(open_sidebar.count()):
        other = open_sidebar.nth(index).bounding_box()
        if other is None:
            continue
        overlaps = (
            box["x"] < other["x"] + other["width"]
            and other["x"] < box["x"] + box["width"]
            and box["y"] < other["y"] + other["height"]
            and other["y"] < box["y"] + box["height"]
        )
        assert not overlaps, f"{name} {box} overlaps the Open sidebar button {other}"


def test_ac021_phone_390_channel_back_clear_of_open_sidebar(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 the channel back control does not sit under the Open sidebar button."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_channel_path(conversation)}")
    back = page.get_by_test_id(CHAT_TEST_IDS["back"])
    expect(back).to_be_visible(timeout=30_000)
    _assert_clear_of_open_sidebar(page, back, "channel back")
    _assert_no_horizontal_scroll(page)


def _assert_chat_message_is_readable(page: Page) -> None:
    """Message text is at least 16px and the page does not scroll sideways."""
    body = page.get_by_test_id(CHAT_TEST_IDS["message"]).locator("p").first
    expect(body).to_be_visible(timeout=30_000)
    size = body.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
    assert size >= 16, f"message font is {size}px"
    _assert_no_horizontal_scroll(page)


def test_t007b_desktop_1280_chat_type_is_readable(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 1280x800 a channel message uses the larger chat type scale."""
    page = page_as(ANNA, viewport=DESKTOP)
    page.goto(f"{team_chat_server.base_url}{_channel_path(conversation)}")
    _assert_chat_message_is_readable(page)


def test_t007b_phone_390_chat_type_is_readable(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 a channel message uses the larger chat type scale."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_channel_path(conversation)}")
    _assert_chat_message_is_readable(page)


def test_t007b_thread_close_returns_to_the_channel(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """The thread close control leaves the thread at both gate viewports."""
    for viewport in (DESKTOP, PHONE):
        page = page_as(ANNA, viewport=viewport)
        page.goto(f"{team_chat_server.base_url}{_thread_path(conversation)}")
        close = page.get_by_test_id(CHAT_TEST_IDS["threadClose"])
        expect(close).to_be_visible(timeout=30_000)
        close.click()
        expect(page).to_have_url(
            f"{team_chat_server.base_url}{_channel_path(conversation)}", timeout=15_000
        )
        expect(page.get_by_test_id(CHAT_TEST_IDS["threadPane"])).to_be_hidden()
        _assert_no_horizontal_scroll(page)


def test_ac021_phone_390_thread_back_clear_of_open_sidebar(
    team_chat_server: TeamChatServer, page_as, conversation: dict[str, Any]
) -> None:
    """At 390x844 the thread back control does not sit under the Open sidebar button."""
    page = page_as(ANNA, viewport=PHONE)
    page.goto(f"{team_chat_server.base_url}{_thread_path(conversation)}")
    thread = page.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(thread.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text="Passt")).to_be_visible(
        timeout=30_000
    )
    _assert_clear_of_open_sidebar(
        page, thread.get_by_test_id(CHAT_TEST_IDS["threadBack"]), "thread back"
    )
    _assert_no_horizontal_scroll(page)
