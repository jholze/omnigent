"""Tests for the shared queued-follow-ups registry (``omnigent/server/queued_messages.py``).

Broadcasts are observed through a real ``session_stream.subscribe``
collector — the same pub/sub path the SSE route consumes. Every
``session.queue`` event carries the FULL merged list in flush order, so
assertions compare whole payloads.

The expiry grace timer is what keeps a closed window from leaving phantom
entries that block other clients' flushes while a transient reconnect stays
invisible; tests shrink ``_DETACH_GRACE_S`` via monkeypatch the same way
``test_presence.py`` shrinks its leave grace.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from omnigent.server import queued_messages
from omnigent.server.schemas import QueuedMessageInput
from tests.server.helpers import start_session_stream_collector

pytestmark = pytest.mark.asyncio

CONV = "conv_queue_test"
DESKTOP = "c_desktop"
BROWSER = "c_browser"
ALICE = "alice@example.com"


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    """Isolate the module-global registry (and its timers) per test."""
    queued_messages.reset_for_tests()
    yield
    queued_messages.reset_for_tests()


def _msg(queue_id: str, text: str, **overrides: Any) -> QueuedMessageInput:
    return QueuedMessageInput(queue_id=queue_id, text=text, **overrides)


def _order(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Flush order as ``(client_id, text)`` pairs."""
    return [(m["client_id"], m["text"]) for m in event["messages"]]


async def test_replace_broadcasts_full_state() -> None:
    """Publishing a share broadcasts the complete merged list with ownership."""
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV,
            client_id=DESKTOP,
            user_id=ALICE,
            messages=[_msg("q_1", "desktop follow-up", attachments=["shot.png"], stable_id="s1")],
        )
        event = await collector.next_event()
        assert event["type"] == "session.queue"
        assert event["conversation_id"] == CONV
        assert event["messages"] == [
            {
                "queue_id": "q_1",
                "client_id": DESKTOP,
                "seq": 1,
                "text": "desktop follow-up",
                "attachments": ["shot.png"],
                "stable_id": "s1",
                "created_by": ALICE,
                "requires_retry": False,
            }
        ]
        # The snapshot-on-connect payload is the same builder as the broadcast.
        assert queued_messages.snapshot(CONV) == event
    finally:
        await collector.stop()


async def test_clients_merge_in_publish_order_and_each_only_replaces_its_own() -> None:
    """Two windows' shares interleave FIFO; one window cannot drop the other's entry."""
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "desktop follow-up")]
        )
        await collector.next_event()
        queued_messages.replace(
            CONV, client_id=BROWSER, user_id=None, messages=[_msg("q_1", "browser follow-up")]
        )
        both = await collector.next_event()
        assert _order(both) == [(DESKTOP, "desktop follow-up"), (BROWSER, "browser follow-up")]

        # Desktop flushed its head: its share empties, the browser's stays.
        queued_messages.replace(CONV, client_id=DESKTOP, user_id=None, messages=[])
        remaining = await collector.next_event()
        assert _order(remaining) == [(BROWSER, "browser follow-up")]

        # Same queue_id on a different client is a distinct entry, not an overwrite.
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "desktop again")]
        )
        again = await collector.next_event()
        assert _order(again) == [(BROWSER, "browser follow-up"), (DESKTOP, "desktop again")]
    finally:
        await collector.stop()


async def test_unchanged_republish_is_silent() -> None:
    """Republishing an identical share must not spam co-viewers with no-op events."""
    collector = await start_session_stream_collector(CONV)
    try:
        messages = [_msg("q_1", "same")]
        queued_messages.replace(CONV, client_id=DESKTOP, user_id=None, messages=messages)
        await collector.next_event()
        queued_messages.replace(CONV, client_id=DESKTOP, user_id=None, messages=messages)
        await collector.assert_no_event(within=0.2)
    finally:
        await collector.stop()


async def test_reorder_keeps_slots_relative_to_other_clients() -> None:
    """Reordering one window's entries keeps its position among other windows' entries."""
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV,
            client_id=DESKTOP,
            user_id=None,
            messages=[_msg("q_1", "d1"), _msg("q_2", "d2")],
        )
        await collector.next_event()
        queued_messages.replace(
            CONV, client_id=BROWSER, user_id=None, messages=[_msg("q_1", "b1")]
        )
        await collector.next_event()
        # Desktop swaps its two rows: they refill the same two slots, so the
        # browser's entry stays behind both.
        queued_messages.replace(
            CONV,
            client_id=DESKTOP,
            user_id=None,
            messages=[_msg("q_2", "d2"), _msg("q_1", "d1")],
        )
        swapped = await collector.next_event()
        assert _order(swapped) == [(DESKTOP, "d2"), (DESKTOP, "d1"), (BROWSER, "b1")]
        # A new entry appended at the tail lands after everything.
        queued_messages.replace(
            CONV,
            client_id=DESKTOP,
            user_id=None,
            messages=[_msg("q_2", "d2"), _msg("q_1", "d1"), _msg("q_3", "d3")],
        )
        appended = await collector.next_event()
        assert _order(appended) == [
            (DESKTOP, "d2"),
            (DESKTOP, "d1"),
            (BROWSER, "b1"),
            (DESKTOP, "d3"),
        ]
    finally:
        await collector.stop()


async def test_requires_retry_and_edits_propagate() -> None:
    """A failed send and an edited text reach other windows without changing slots."""
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "v1")]
        )
        first = await collector.next_event()
        queued_messages.replace(
            CONV,
            client_id=DESKTOP,
            user_id=None,
            messages=[_msg("q_1", "v1", requires_retry=True)],
        )
        failed = await collector.next_event()
        assert failed["messages"][0]["requires_retry"] is True
        assert failed["messages"][0]["seq"] == first["messages"][0]["seq"]
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "v2")]
        )
        edited = await collector.next_event()
        assert edited["messages"][0]["text"] == "v2"
        assert edited["messages"][0]["requires_retry"] is False
        assert edited["messages"][0]["seq"] == first["messages"][0]["seq"]
    finally:
        await collector.stop()


async def test_cleared_share_republished_while_detached_gets_full_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing a detached share cancels its expiry; a later share starts a fresh window."""
    monkeypatch.setattr(queued_messages, "_DETACH_GRACE_S", 0.3)
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "d1")]
        )
        await collector.next_event()
        await asyncio.sleep(0.15)
        queued_messages.replace(CONV, client_id=DESKTOP, user_id=None, messages=[])
        assert (await collector.next_event())["messages"] == []
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_2", "d2")]
        )
        await collector.next_event()
        # The first share's timer would have fired here; the new share must outlive it.
        await collector.assert_no_event(within=0.2)
        assert _order(queued_messages.snapshot(CONV)) == [(DESKTOP, "d2")]
        expired = await collector.next_event()
        assert expired["messages"] == []
    finally:
        await collector.stop()


async def test_share_expires_after_last_stream_detaches(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed window's entries disappear after the grace window, not immediately."""
    monkeypatch.setattr(queued_messages, "_DETACH_GRACE_S", 0.05)
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.attach(CONV, client_id=DESKTOP, user_id=None)
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "d1")]
        )
        await collector.next_event()
        queued_messages.detach(CONV, client_id=DESKTOP, user_id=None)
        # Still listed inside the grace window: a transient reconnect must
        # not flicker the other window's strip.
        assert _order(queued_messages.snapshot(CONV)) == [(DESKTOP, "d1")]
        expired = await collector.next_event()
        assert expired["messages"] == []
        assert queued_messages.snapshot(CONV)["messages"] == []
    finally:
        await collector.stop()


async def test_reattach_within_grace_keeps_share(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnecting before the grace elapses cancels the expiry."""
    monkeypatch.setattr(queued_messages, "_DETACH_GRACE_S", 0.1)
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.attach(CONV, client_id=DESKTOP, user_id=None)
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "d1")]
        )
        await collector.next_event()
        queued_messages.detach(CONV, client_id=DESKTOP, user_id=None)
        queued_messages.attach(CONV, client_id=DESKTOP, user_id=None)
        await collector.assert_no_event(within=0.3)
        assert _order(queued_messages.snapshot(CONV)) == [(DESKTOP, "d1")]
    finally:
        await collector.stop()


async def test_share_without_stream_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A publish from a client holding no stream is kept only for the grace window."""
    monkeypatch.setattr(queued_messages, "_DETACH_GRACE_S", 0.05)
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(
            CONV, client_id=DESKTOP, user_id=None, messages=[_msg("q_1", "d1")]
        )
        await collector.next_event()
        expired = await collector.next_event()
        assert expired["messages"] == []
    finally:
        await collector.stop()


async def test_shares_are_scoped_per_user() -> None:
    """The same client id under another user is a different share."""
    collector = await start_session_stream_collector(CONV)
    try:
        queued_messages.replace(CONV, client_id="c_1", user_id=ALICE, messages=[_msg("q_1", "a")])
        await collector.next_event()
        queued_messages.replace(
            CONV, client_id="c_1", user_id="bob@example.com", messages=[_msg("q_1", "b")]
        )
        both = await collector.next_event()
        assert [(m["created_by"], m["text"]) for m in both["messages"]] == [
            (ALICE, "a"),
            ("bob@example.com", "b"),
        ]
    finally:
        await collector.stop()
