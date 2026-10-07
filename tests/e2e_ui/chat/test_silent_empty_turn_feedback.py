"""A turn the user watched start and stop must never end with no feedback.

Mid-session the user sends a prompt; Omnigent shows the working indicator, then
stops with no assistant reply, no error, and no notice. The trigger
is a completed-but-empty model response: the stream completes normally but its
only output item is an empty assistant message, so the turn resolves silently.

The final assertion fails on the unfixed build (nothing renders for turn 2) and
passes once the empty turn surfaces feedback: a reply, an error pill, or notice.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import configure_mock_llm

_COMPOSER = "Send a message…"
_ASSISTANT = '[data-testid="message-bubble"][data-role="assistant"]'
_USER = '[data-testid="message-bubble"][data-role="user"]'
_WORKING = '[data-testid="working-indicator"]'

_TURN1_TOKEN = "SILENT-STOP-TURN-ONE"
_TURN2_TOKEN = "SILENT-STOP-TURN-TWO"


def _send(page: Page, text: str) -> None:
    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible()
    composer.fill(text)
    page.get_by_role("button", name="Send", exact=True).click()


@pytest.mark.timeout(300)
def test_turn_that_stops_must_leave_feedback(
    request: pytest.FixtureRequest,
    seeded_session: tuple[str, str],
    mock_llm_server_url: str,
) -> None:
    base_url, session_id = seeded_session

    # Both queues are deep: turn 1 so a background title request can't drain the
    # one real reply, turn 2 so a fix that retries the empty turn still runs out
    # on the same fault and must surface feedback rather than loop silently.
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "hello from turn one"}] * 3,
        key="silent-stop-t1",
        match=_TURN1_TOKEN,
    )
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": ""}] * 3,
        key="silent-stop-t2",
        match=_TURN2_TOKEN,
    )

    page = request.getfixturevalue("page")
    page.goto(f"{base_url}/c/{session_id}")

    _send(page, f"{_TURN1_TOKEN} say hello")
    expect(page.locator(_ASSISTANT).first).to_be_visible(timeout=60_000)
    expect(page.locator(_WORKING)).to_have_count(0, timeout=60_000)

    _send(page, f"{_TURN2_TOKEN} tell me more")
    expect(page.locator(_USER)).to_have_count(2, timeout=15_000)
    expect(page.locator(_WORKING).first).to_be_visible(timeout=30_000)
    expect(page.locator(_WORKING)).to_have_count(0, timeout=90_000)

    feedback = page.locator(_ASSISTANT).nth(1).or_(page.get_by_test_id("error-pill").first).first
    expect(
        feedback,
        "turn 2 started and stopped but left no feedback at all: no assistant "
        "reply and no error/notice pill rendered (silent stop)",
    ).to_be_visible(timeout=15_000)
