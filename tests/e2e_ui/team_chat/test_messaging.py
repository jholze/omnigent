"""E2E: two people write and read team chat messages in real browsers (T-007).

Anna and Jens each get their own browser context on one multi-user server.
Messages travel over the real REST routes, and Jens's view updates through
the ``/v1/sessions/updates`` socket.

Human principals have no ``display_name`` in the bootstrap directory, so the
UI labels an author with their principal id. The tests read the expected
label from the directory instead of hard-coding "Anna".
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Dialog, Locator, Page, Route, expect

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

_UPDATES_SOCKET_PATH = "/v1/sessions/updates"
_MESSAGES_URL = re.compile(r"/v1/chat/channels/[^/]+/messages(\?.*)?$")
_DM_URL = re.compile(r"/v1/chat/dms$")
_CHANNEL_ROUTE = re.compile(r"/chat/([^/?#]+)$")
_CLOCK = re.compile(r"^\d{2}:\d{2}$")
_LOAD_TIMEOUT_MS = 30_000
_LIVE_TIMEOUT_MS = 15_000


def _open_chat(page: Page, server: TeamChatServer, path: str) -> None:
    """Load *path* and wait until the updates socket that carries chat frames opens."""
    with page.expect_websocket(
        lambda socket: _UPDATES_SOCKET_PATH in socket.url, timeout=_LOAD_TIMEOUT_MS
    ):
        page.goto(f"{server.base_url}{path}")
    expect(page.get_by_test_id(CHAT_TEST_IDS["page"]).first).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )


def _open_channel(page: Page, server: TeamChatServer, channel_id: str) -> Locator:
    """Open a channel and return its composer input once the list has loaded."""
    _open_chat(page, server, f"/chat/{quote(channel_id, safe='')}")
    expect(page.get_by_test_id(CHAT_TEST_IDS["messageList"]).first).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )
    composer_input = page.get_by_test_id(CHAT_TEST_IDS["composerInput"]).first
    expect(composer_input).to_be_visible()
    return composer_input


def _send(composer_input: Locator, body: str) -> None:
    composer_input.fill(body)
    composer_input.press("Enter")


def _message_row(scope: Page | Locator, text: str) -> Locator:
    return scope.get_by_test_id(CHAT_TEST_IDS["message"]).filter(has_text=text)


def _message_body(row: Locator) -> Locator:
    """The rendered body paragraph, excluding the row's thread reply controls."""
    return row.locator("p")


def _directory_label(bootstrap: dict[str, Any], principal_id: str) -> str:
    """The author label the UI shows: directory ``display_name``, else the id."""
    for entry in bootstrap["directory"]:
        if entry["id"] == principal_id and entry.get("display_name"):
            return entry["display_name"]
    return principal_id


def _record_dialogs(page: Page) -> list[str]:
    """Collect every alert/confirm/prompt the page opens; dismiss each one."""
    opened: list[str] = []

    def _on_dialog(dialog: Dialog) -> None:
        opened.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", _on_dialog)
    return opened


def _shared_channel(server: TeamChatServer) -> dict[str, Any]:
    """A fresh public channel that Anna created and Jens joined."""
    channel = seed_channel(server, as_user=ANNA)
    join_channel(server, as_user=JENS, channel_id=channel["id"])
    return channel


def test_ac_013a_jens_sees_annas_message_live_with_author_and_time(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Anna sends in #general; Jens sees it within 1 s without reload, with author and time."""
    anna_bootstrap = fetch_bootstrap(team_chat_server, as_user=ANNA)
    jens_bootstrap = fetch_bootstrap(team_chat_server, as_user=JENS)
    general = next(item for item in jens_bootstrap["channels"] if item["name"] == "general")
    author_label = _directory_label(jens_bootstrap, anna_bootstrap["me"])

    jens = page_as(JENS)
    _open_channel(jens, team_chat_server, general["id"])
    anna = page_as(ANNA)
    anna_input = _open_channel(anna, team_chat_server, general["id"])

    _send(anna_input, "Deploy heute 16 Uhr?")

    # The 1 s budget runs from Anna's Enter and covers her POST plus the frame.
    row = _message_row(jens, "Deploy heute 16 Uhr?")
    expect(row).to_be_visible(timeout=1_000)
    expect(row).to_contain_text(author_label)
    expect(row.locator("time").first).to_have_text(_CLOCK)


def test_ac_020a_script_and_markdown_render_as_text_and_url_as_link(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens sees ``<script>`` and ``**fett**`` as text, one https link, and no script runs."""
    channel = _shared_channel(team_chat_server)
    body = "<script>alert(1)</script> **fett** https://example.com"

    jens = page_as(JENS)
    jens_dialogs = _record_dialogs(jens)
    _open_channel(jens, team_chat_server, channel["id"])
    anna = page_as(ANNA)
    anna_dialogs = _record_dialogs(anna)
    _send(_open_channel(anna, team_chat_server, channel["id"]), body)

    row = _message_row(jens, "**fett**")
    expect(row).to_be_visible(timeout=_LIVE_TIMEOUT_MS)
    expect(row).to_contain_text("<script>alert(1)</script> **fett**")
    expect(row.locator("script")).to_have_count(0)
    expect(row.locator("strong, b, em")).to_have_count(0)
    link = row.get_by_role("link", name="https://example.com", exact=True)
    expect(link).to_have_attribute("href", "https://example.com")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(_message_body(row).get_by_role("link")).to_have_count(1)
    assert jens_dialogs == []
    assert anna_dialogs == []


def test_ac_020a_javascript_url_is_not_a_link(team_chat_server: TeamChatServer, page_as) -> None:
    """A ``javascript:`` URL stays plain text; clicking it runs nothing."""
    channel = _shared_channel(team_chat_server)
    seed_message(
        team_chat_server,
        as_user=ANNA,
        channel_id=channel["id"],
        body='javascript:alert(2) <a href="javascript:alert(3)">klick</a>',
    )

    jens = page_as(JENS)
    dialogs = _record_dialogs(jens)
    _open_channel(jens, team_chat_server, channel["id"])

    row = _message_row(jens, "javascript:alert(2)")
    expect(row).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(row).to_contain_text('<a href="javascript:alert(3)">klick</a>')
    body = _message_body(row)
    expect(body).to_contain_text("javascript:alert(2)")
    expect(body.get_by_role("link")).to_have_count(0)
    expect(body.locator("a")).to_have_count(0)
    body.get_by_text("javascript:alert(2)").first.click()
    assert dialogs == []


def test_ac_021a_dm_opens_once_and_appears_for_jens_after_first_message(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Opening a DM with Jens twice gives one id; Jens then lists it under Direktnachrichten."""
    # Jens joins #general first, so Anna's directory lists him.
    jens_id = fetch_bootstrap(team_chat_server, as_user=JENS)["me"]

    jens = page_as(JENS)
    _open_chat(jens, team_chat_server, "/chat")
    anna = page_as(ANNA)
    _open_chat(anna, team_chat_server, "/chat")

    def _open_dm_with_jens() -> str:
        anna.get_by_test_id(CHAT_TEST_IDS["newDm"]).click()
        dialog = anna.get_by_test_id(CHAT_TEST_IDS["newDmDialog"])
        expect(dialog).to_be_visible()
        person = dialog.locator(
            f'[data-testid="{CHAT_TEST_IDS["newDmPerson"]}"][data-principal-id="{jens_id}"]'
        )
        with anna.expect_response(
            lambda response: (
                _DM_URL.search(response.url) is not None and response.request.method == "POST"
            )
        ) as response_info:
            person.click()
        response = response_info.value
        assert response.ok, f"POST /v1/chat/dms answered {response.status}"
        dm_id = response.json()["id"]
        anna.wait_for_url(_CHANNEL_ROUTE)
        route_match = _CHANNEL_ROUTE.search(anna.url)
        assert route_match is not None
        assert route_match.group(1) == quote(dm_id, safe="")
        return dm_id

    first_dm_id = _open_dm_with_jens()
    second_dm_id = _open_dm_with_jens()
    assert second_dm_id == first_dm_id

    anna_input = anna.get_by_test_id(CHAT_TEST_IDS["composerInput"]).first
    expect(anna_input).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(anna.get_by_test_id(CHAT_TEST_IDS["messageList"]).first).to_be_visible(
        timeout=_LOAD_TIMEOUT_MS
    )
    # Fill can land on a composer that the next bootstrap remounts. Put the
    # text back until the Send button keeps it, then click that button.
    send = anna.get_by_test_id(CHAT_TEST_IDS["composer"]).first.get_by_role(
        "button", name="Senden"
    )
    body = "Hallo Jens, kurze Frage"
    kept = False
    for _attempt in range(3):
        anna_input.fill(body)
        try:
            expect(anna_input).to_have_value(body, timeout=2_000)
            expect(send).to_be_enabled(timeout=2_000)
        except AssertionError:
            continue
        send.click()
        kept = True
        break
    assert kept, "composer did not keep the DM text long enough to send"
    expect(_message_row(anna, "Hallo Jens, kurze Frage")).to_have_attribute(
        "data-status", "sent", timeout=_LOAD_TIMEOUT_MS
    )

    jens_dms = jens.get_by_role("region", name="Direktnachrichten")
    dm_link = jens_dms.locator(f'a[href$="/chat/{quote(first_dm_id, safe="")}"]')
    expect(dm_link).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    dm_link.click()
    expect(_message_row(jens, "Hallo Jens, kurze Frage")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)


def test_ac_023a_thread_reply_shows_live_count_and_reply(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens answers "Passt" in m1's thread; Anna sees "1 Antwort" and the thread shows it.

    Live only: the message wire has no ``reply_count``, so a full reload does
    not restore the count.
    """
    channel = _shared_channel(team_chat_server)
    root = seed_message(
        team_chat_server, as_user=ANNA, channel_id=channel["id"], body="Release heute Abend?"
    )

    anna = page_as(ANNA)
    _open_channel(anna, team_chat_server, channel["id"])
    anna_root = _message_row(anna, "Release heute Abend?")
    expect(anna_root).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(anna_root.get_by_test_id(CHAT_TEST_IDS["replyCount"])).to_have_count(0)

    jens = page_as(JENS)
    _open_chat(
        jens,
        team_chat_server,
        f"/chat/{quote(channel['id'], safe='')}/t/{quote(root['id'], safe='')}",
    )
    thread = jens.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(thread).to_contain_text("Release heute Abend?", timeout=_LOAD_TIMEOUT_MS)
    _send(thread.get_by_test_id(CHAT_TEST_IDS["composerInput"]), "Passt")
    expect(_message_row(thread, "Passt")).to_have_attribute("data-status", "sent")

    reply_count = anna_root.get_by_test_id(CHAT_TEST_IDS["replyCount"])
    expect(reply_count).to_have_text("1 Antwort", timeout=_LIVE_TIMEOUT_MS)
    reply_count.click()
    anna_thread = anna.get_by_test_id(CHAT_TEST_IDS["threadPane"])
    expect(_message_row(anna_thread, "Passt")).to_be_visible(timeout=_LOAD_TIMEOUT_MS)


def test_ac_084a_failed_send_retries_with_same_client_id_and_stores_once(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """A 503 shows "nicht gesendet"; after recovery one "erneut senden" stores one message."""
    channel = seed_channel(team_chat_server, as_user=ANNA)
    anna = page_as(ANNA)
    anna_input = _open_channel(anna, team_chat_server, channel["id"])

    posted: list[dict[str, Any]] = []
    server_down = True

    def _route_messages(route: Route) -> None:
        if route.request.method != "POST":
            route.continue_()
            return
        posted.append(route.request.post_data_json)
        if server_down:
            route.fulfill(
                status=503, content_type="application/json", body='{"detail":"unavailable"}'
            )
        else:
            route.continue_()

    anna.route(_MESSAGES_URL, _route_messages)
    _send(anna_input, "Hallo")

    row = _message_row(anna, "Hallo")
    expect(row.get_by_test_id(CHAT_TEST_IDS["sendFailed"])).to_contain_text(
        "nicht gesendet", timeout=_LIVE_TIMEOUT_MS
    )
    retry = row.get_by_role("button", name="erneut senden")
    expect(retry).to_be_visible()
    assert len(posted) == 1
    assert list_messages(team_chat_server, as_user=ANNA, channel_id=channel["id"]) == []

    server_down = False
    retry.click()

    sent_rows = anna.locator(
        f'[data-testid="{CHAT_TEST_IDS["message"]}"][data-status="sent"]'
    ).filter(has_text="Hallo")
    expect(sent_rows).to_have_count(1, timeout=_LIVE_TIMEOUT_MS)
    expect(row).to_have_count(1)
    expect(anna.get_by_test_id(CHAT_TEST_IDS["sendFailed"])).to_have_count(0)
    assert len(posted) == 2
    assert posted[1]["client_id"] == posted[0]["client_id"]
    stored = list_messages(team_chat_server, as_user=ANNA, channel_id=channel["id"])
    assert [(item["body"], item["client_id"]) for item in stored] == [
        ("Hallo", posted[0]["client_id"])
    ]
