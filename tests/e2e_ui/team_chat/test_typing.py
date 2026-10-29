"""E2E: Jens sees "Anna schreibt…" while Anna types, and it is gone 4 s later (AC-032a).

Anna and Jens each get their own browser context on one multi-user server.
The typing signal travels over ``POST .../typing`` and Jens's
``/v1/sessions/updates`` socket. Nothing is posted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    fetch_bootstrap,
    list_messages,
)
from tests.e2e_ui.team_chat.test_messaging import _open_channel
from tests.e2e_ui.team_chat.test_viewports import DESKTOP, PHONE, _assert_no_horizontal_scroll

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_LIVE_TIMEOUT_MS = 15_000
# TYPING_HIDE_MS in web/src/chat/typingStore.ts.
_TYPING_HIDE_S = 4.0
# Frame latency between Anna's keystroke and Jens's store starting its timer.
_HIDE_SLACK_S = 1.0
# Continuous typing: 22 keys 100 ms apart, about 2.2 s, all inside one 3 s send window.
_TYPED_BURST = "Deploy heute um 16 Uhr"
_KEY_DELAY_MS = 100
# After the last key Jens still sees the indicator here (trailing send) ...
_STILL_SHOWN_AFTER_S = 3.9
# ... and it is gone by here.
_GONE_BY_S = 7.5


def _person_label(bootstrap: dict[str, Any], principal_id: str) -> str:
    """The name the indicator shows: ``display_name``, ``handle``, else the id's local part.

    Mirrors ``chatPersonName`` in ``web/src/chat/NeedsYouView.tsx``.
    """
    for entry in bootstrap["directory"]:
        if entry["id"] != principal_id:
            continue
        name = (entry.get("display_name") or "").strip() or (entry.get("handle") or "").strip()
        if name:
            return name
    local = principal_id.split("@", 1)[0].strip() or principal_id
    return local[:1].upper() + local[1:]


def _message_ids(server: TeamChatServer, channel_id: str) -> list[str]:
    """Ids of the newest stored page in *channel_id*, as Jens reads it."""
    return [item["id"] for item in list_messages(server, as_user=JENS, channel_id=channel_id)]


@_VIEWPORTS
def test_ac_032a_jens_sees_anna_typing_then_it_disappears(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
) -> None:
    """Anna types in #general without sending; Jens sees "{Anna} schreibt…", gone after 4 s."""
    anna_id = fetch_bootstrap(team_chat_server, as_user=ANNA)["me"]
    jens_bootstrap = fetch_bootstrap(team_chat_server, as_user=JENS)
    general = next(item for item in jens_bootstrap["channels"] if item["name"] == "general")
    label = _person_label(jens_bootstrap, anna_id)
    stored_before = _message_ids(team_chat_server, general["id"])

    jens = page_as(JENS, viewport=viewport)
    _open_channel(jens, team_chat_server, general["id"])
    rows_before = jens.get_by_test_id(CHAT_TEST_IDS["message"]).count()
    anna = page_as(ANNA, viewport=viewport)
    anna_input = _open_channel(anna, team_chat_server, general["id"])

    # One input event, so exactly one active frame starts Jens's hide timer.
    anna_input.fill("Deploy heute 16 Uhr?")
    typed_at = time.monotonic()

    indicator = jens.get_by_test_id(CHAT_TEST_IDS["typingIndicator"])
    expect(indicator).to_have_text(f"{label} schreibt…", timeout=_LIVE_TIMEOUT_MS)
    _assert_no_horizontal_scroll(jens)

    expect(indicator).to_have_count(0, timeout=_LIVE_TIMEOUT_MS)
    hidden_after = time.monotonic() - typed_at
    assert hidden_after >= _TYPING_HIDE_S - _HIDE_SLACK_S, (
        f"indicator vanished after {hidden_after:.1f}s, before the 4 s hide window"
    )
    # Anna's draft is still there: the indicator expired, she did not send or clear.
    expect(anna_input).to_have_value("Deploy heute 16 Uhr?")

    # A typing frame is not a post.
    expect(jens.get_by_test_id(CHAT_TEST_IDS["message"])).to_have_count(rows_before)
    assert _message_ids(team_chat_server, general["id"]) == stored_before
    _assert_no_horizontal_scroll(jens)


@_VIEWPORTS
def test_ac_032a_indicator_counts_from_the_last_keystroke_not_the_first(
    team_chat_server: TeamChatServer,
    page_as: Callable[..., Page],
    viewport: dict[str, int],
) -> None:
    """Anna types for about 2 s then stops; Jens sees her typing 3.9 s later, not 7.5 s later."""
    anna_id = fetch_bootstrap(team_chat_server, as_user=ANNA)["me"]
    jens_bootstrap = fetch_bootstrap(team_chat_server, as_user=JENS)
    general = next(item for item in jens_bootstrap["channels"] if item["name"] == "general")
    label = _person_label(jens_bootstrap, anna_id)
    stored_before = _message_ids(team_chat_server, general["id"])

    jens = page_as(JENS, viewport=viewport)
    _open_channel(jens, team_chat_server, general["id"])
    rows_before = jens.get_by_test_id(CHAT_TEST_IDS["message"]).count()
    anna = page_as(ANNA, viewport=viewport)
    anna_input = _open_channel(anna, team_chat_server, general["id"])

    typing_started = time.monotonic()
    anna_input.press_sequentially(_TYPED_BURST, delay=_KEY_DELAY_MS)
    last_key_at = time.monotonic()
    typing_took = last_key_at - typing_started
    assert typing_took < 3.0, f"typing took {typing_took:.1f}s, past one 3 s send window"

    indicator = jens.get_by_test_id(CHAT_TEST_IDS["typingIndicator"])
    expect(indicator).to_have_text(f"{label} schreibt…", timeout=_LIVE_TIMEOUT_MS)

    time.sleep(max(0.0, last_key_at + _STILL_SHOWN_AFTER_S - time.monotonic()))
    assert indicator.count() == 1, (
        f"indicator gone {_STILL_SHOWN_AFTER_S}s after the last key; hide timer ran from the first"
    )
    _assert_no_horizontal_scroll(jens)

    remaining_ms = max(0.0, last_key_at + _GONE_BY_S - time.monotonic()) * 1000
    expect(indicator).to_have_count(0, timeout=remaining_ms)

    # A typing frame is not a post.
    expect(anna_input).to_have_value(_TYPED_BURST)
    expect(jens.get_by_test_id(CHAT_TEST_IDS["message"])).to_have_count(rows_before)
    assert _message_ids(team_chat_server, general["id"]) == stored_before
    _assert_no_horizontal_scroll(jens)
