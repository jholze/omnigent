"""E2E: a reader who was offline gets the missed messages back (AC-082a).

Jens's browser context goes offline with Playwright's network emulation
while Anna posts over the real REST route. Back online, the page catches up
from its changes cursor without a reload.
"""

from __future__ import annotations

import re
import time
import uuid

from playwright.sync_api import expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    TeamChatServer,
    join_channel,
    seed_channel,
    seed_message,
)
from tests.e2e_ui.team_chat.test_messaging import _open_channel

_OFFLINE_S = 30.0
_CATCH_UP_TIMEOUT_MS = 3_000


def test_ac_082a_offline_reader_sees_missed_messages_once_in_order(
    team_chat_server: TeamChatServer, page_as
) -> None:
    """Jens offline 30 s while Anna posts 3; back online he sees each once, in order, in 3 s."""
    channel = seed_channel(team_chat_server, as_user=ANNA)
    join_channel(team_chat_server, as_user=JENS, channel_id=channel["id"])
    jens = page_as(JENS)
    _open_channel(jens, team_chat_server, channel["id"])

    run = uuid.uuid4().hex[:8]
    bodies = [f"verpasst {run} eins", f"verpasst {run} zwei", f"verpasst {run} drei"]
    missed = (
        jens.get_by_test_id(CHAT_TEST_IDS["messageList"])
        .first.get_by_test_id(CHAT_TEST_IDS["message"])
        .filter(has_text=re.compile(f"verpasst {run} "))
    )

    jens.context.set_offline(True)
    offline_since = time.monotonic()
    for body in bodies:
        seed_message(team_chat_server, as_user=ANNA, channel_id=channel["id"], body=body)
    jens.wait_for_timeout(max(0.0, _OFFLINE_S - (time.monotonic() - offline_since)) * 1_000)
    expect(missed).to_have_count(0)

    jens.context.set_offline(False)
    expect(missed).to_have_count(len(bodies), timeout=_CATCH_UP_TIMEOUT_MS)
    shown = [text.strip() for text in missed.locator("p").all_inner_texts()]
    assert shown == bodies
