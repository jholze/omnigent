"""E2E: agent controls in an open Agent-Thread (T-121, FR-049, FR-050, FR-052).

Slash commands are typed into the thread composer: an EDIT caller's
``/interrupt`` posts the ``interrupt`` session event (AC-049a). This fixture
has no runner to cancel the turn, so the test then reports ``idle`` the way
a harness acknowledges a cancel, and the chip must follow in 1 s. A READ
viewer's ``/retry`` shows the rights hint and sends nothing (AC-050a).
"Session öffnen" opens the transcript read-only for a READ grantee
(AC-052a). The session side is driven as in ``test_agent_thread.py``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
from playwright.sync_api import Locator, Request, expect

from tests.e2e_ui.team_chat.conftest import ANNA, CHAT_TEST_IDS, JENS, TeamChatServer
from tests.e2e_ui.team_chat.test_agent_thread import (
    _LOAD_TIMEOUT_MS,
    BOB,
    _agent_channel,
    _answer,
    _open_channel,
    _open_live_thread,
    _open_thread,
    _set_status,
    _start_agent_thread,
    _thread_chip,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
    _assert_touch_target,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_NO_EDIT_HINT = "Keine Berechtigung: nur Aufrufer und Bearbeiter"
_READ_ONLY_PLACEHOLDER = "You have read-only access to this session"
_CONTROL_KEYS = (
    "agentControlInterrupt",
    "agentControlRetry",
    "agentControlStop",
    "agentControlDismiss",
)
_EVENTS_URL = re.compile(r"/v1/sessions/[^/]+/events$")
_MESSAGES_URL = re.compile(r"/v1/chat/channels/[^/]+/messages(\?.*)?$")
_CHIP_BUDGET_MS = 1_000
# Window in which a wrongly sent command would have left the page.
_NO_SEND_WINDOW_MS = 1_000


def _send_in_thread(pane: Locator, body: str) -> None:
    composer = pane.get_by_test_id(CHAT_TEST_IDS["composer"])
    composer.get_by_test_id(CHAT_TEST_IDS["composerInput"]).fill(body)
    composer.get_by_role("button", name="Senden").click()


def _event_type(request: Request) -> Any:
    try:
        return json.loads(request.post_data or "{}").get("type")
    except json.JSONDecodeError:
        return None


def _assert_controls_are_touch_targets(pane: Locator) -> None:
    for key in _CONTROL_KEYS:
        _assert_touch_target(pane.get_by_test_id(CHAT_TEST_IDS[key]), key)


@_VIEWPORTS
def test_ac_049a_interrupt_command_sends_interrupt_and_chip_is_fertig_within_1s(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Session running; Anna sends "/interrupt": interrupt event sent, chip "fertig" in 1 s."""
    channel = _agent_channel(team_chat_server)
    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    chip = _thread_chip(pane)
    _set_status(team_chat_server, thread, "running", response_id="resp_turn")
    expect(chip).to_have_text("arbeitet", timeout=_LOAD_TIMEOUT_MS)
    if viewport == PHONE:
        _assert_controls_are_touch_targets(pane)

    events_path = f"/v1/sessions/{thread.session_id}/events"
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url.endswith(events_path)
            and _event_type(request) == "interrupt"
        ),
        timeout=_LOAD_TIMEOUT_MS,
    ):
        _send_in_thread(pane, "/interrupt")
    # The harness's answer to the interrupt; no runner here sends it.
    _set_status(team_chat_server, thread, "idle", response_id="resp_turn")
    expect(chip).to_have_text("fertig", timeout=_CHIP_BUDGET_MS)
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_050a_read_viewer_retry_command_shows_hint_and_sends_nothing(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Bob (READ) types "/retry": the composer shows the rights hint, no request leaves."""
    channel = _agent_channel(team_chat_server)
    page = page_as(BOB, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    pane = _open_live_thread(page, thread)
    if viewport == PHONE:
        _assert_controls_are_touch_targets(pane)

    sent: list[str] = []
    page.on(
        "request",
        lambda request: (
            sent.append(f"{request.method} {request.url}")
            if request.method == "POST"
            and (_EVENTS_URL.search(request.url) or _MESSAGES_URL.search(request.url))
            else None
        ),
    )
    _send_in_thread(pane, "/retry")
    hint = pane.get_by_test_id(CHAT_TEST_IDS["composerCommandHint"])
    expect(hint).to_have_text(_NO_EDIT_HINT, timeout=_LOAD_TIMEOUT_MS)
    page.wait_for_timeout(_NO_SEND_WINDOW_MS)
    assert sent == [], f"/retry by a READ viewer sent {len(sent)} request(s)"
    _assert_no_horizontal_scroll(page)


@_VIEWPORTS
def test_ac_052a_open_session_navigates_to_transcript_read_only(
    team_chat_server: TeamChatServer, page_as, viewport: dict[str, int]
) -> None:
    """Jens (READ) clicks "Session öffnen": /c/{session_id} shows the transcript, read-only."""
    channel = _agent_channel(team_chat_server)
    page = page_as(JENS, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    thread = _start_agent_thread(team_chat_server, channel["id"])
    marker = f"m052-{uuid.uuid4().hex[:8]}"
    _set_status(team_chat_server, thread, "running", response_id="resp_transcript")
    _answer(team_chat_server, thread, f"Transkript fertig {marker}", "resp_transcript")

    pane = _open_thread(page, thread.token)
    link = pane.get_by_test_id(CHAT_TEST_IDS["threadOpenSession"])
    expect(link).to_have_text("Session öffnen", timeout=_LOAD_TIMEOUT_MS)
    if viewport == PHONE:
        _assert_touch_target(link, "Session öffnen")
    link.click()

    expect(page).to_have_url(f"{team_chat_server.base_url}/c/{thread.session_id}")
    expect(page.get_by_text(marker).first).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    expect(page.get_by_placeholder(_READ_ONLY_PLACEHOLDER)).to_be_visible(timeout=_LOAD_TIMEOUT_MS)
    _assert_no_horizontal_scroll(page)
