"""In-memory stall clock for a running turn.

The live stream calls :func:`observe_published_event` for every event.
Transport keepalives do not move the clock. A real progress event does,
and the first one after a stall queues a single ``session.recovered``.

The server watchdog reads the same clocks. This module does not import
the server, does not interrupt a turn, and does not consult feature flags.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from omnigent.db.workspace_cache import WorkspaceScopedCache

STALL_LABEL_KEY = "omnigent.stall.state"
STALL_LABEL_VALUE = "stalled"

# Keepalives and the stall events themselves are not turn progress.
# Counting them would hide a wedged turn, or clear a stall in the same call
# that published it.
NON_PROGRESS_TYPES = frozenset(
    {
        "session.heartbeat",
        "response.heartbeat",
        "session.presence",
        "session.terminal.activity",
        "session.stalled",
        "session.recovered",
    }
)

# ``waiting`` is a parent whose own turn has finished while children
# are still running. It accepts a new message immediately, so it is
# not an active turn for stall or for the buffered-delivery ack.
_ACTIVE_STATUSES = frozenset({"running"})
_TURN_BOUNDARY_TYPES = frozenset({"response.created", "response.in_progress"})

RecoveredListener = Callable[[str, int], None]


@dataclass
class StallSettings:
    """Thresholds for one watchdog evaluation.

    :param stall_after_s: Silence before a running turn is marked stalled.
    :param interrupt_after_s: Silence before one automatic interrupt, when
        input is buffered and the flag is on.
    :param auto_interrupt: When false, mark and emit only.
    :param cooldown_s: Minimum gap between automatic interrupts of one session.
    """

    stall_after_s: float = 300.0
    interrupt_after_s: float = 600.0
    auto_interrupt: bool = True
    cooldown_s: float = 1800.0


@dataclass
class Clock:
    """Per-session stall memory. Mutate it only under the module lock."""

    status: str = "idle"
    turn_started_at: float | None = None
    last_event_at: float | None = None
    stalled: bool = False
    stalled_emitted: bool = False
    last_auto_interrupt_at: float | None = None
    buffered_count: int = 0
    elicitation_pending: bool = False
    stall_epoch: int = 0
    stall_anchor_ms: int = 0
    pending_recovered: bool = False
    pending_epoch: int = 0
    pending_anchor_ms: int = 0
    turn_response_id: str | None = None
    # True when a Stop/interrupt on this harness drops the runner buffer
    # (claude-native). Auto-interrupt would delete the queued follow-up.
    interrupt_clears_buffer: bool = False


@dataclass(frozen=True)
class Decision:
    """What one evaluation wants the server to do. The clock is unchanged."""

    mark_stalled: bool = False
    emit_stalled: bool = False
    emit_recovered: bool = False
    interrupt: bool = False
    buffered_starts_next_turn: bool = False
    epoch: int = 0
    seconds_since_last_event: float | None = None
    anchor_ms: int = 0


_logger = logging.getLogger(__name__)
_lock = threading.Lock()
_clocks: WorkspaceScopedCache[str, Clock] = WorkspaceScopedCache()
_recovered_listener: RecoveredListener | None = None


def set_recovered_listener(listener: RecoveredListener | None) -> None:
    """Install the server hook that records a recovery outside the stream."""
    global _recovered_listener
    _recovered_listener = listener


def reset_for_tests() -> None:
    """Drop every workspace's clocks. The recovered listener stays installed."""
    with _lock:
        _clocks.clear()


def get_clock(session_id: str) -> Clock | None:
    """Return the live clock, or ``None`` when this session is untracked."""
    with _lock:
        return _clocks.get(session_id)


def session_ids() -> list[str]:
    """Session ids with a clock in the current workspace."""
    with _lock:
        return _clocks.keys()


def note_running(session_id: str, now: float) -> None:
    """Start a clock when a turn is already running and we have not seen it.

    The silence window begins at discovery, so a server restart does not
    immediately mark every running session stalled.
    """
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            _clocks[session_id] = Clock(
                status="running",
                turn_started_at=now,
                last_event_at=now,
            )
            return
        if clock.turn_started_at is None and clock.last_event_at is None:
            clock.turn_started_at = now
            clock.last_event_at = now
            clock.status = "running"


def update_runtime_facts(
    session_id: str,
    *,
    status: str | None,
    elicitation_pending: bool,
    pending_input_count: int,
    interrupt_clears_buffer: bool | None = None,
) -> None:
    """Refresh facts the tick reads from other subsystems."""
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            return
        if status is not None:
            clock.status = status
            if status not in _ACTIVE_STATUSES:
                clock.buffered_count = 0
        clock.elicitation_pending = elicitation_pending
        if pending_input_count > 0:
            clock.buffered_count = max(clock.buffered_count, pending_input_count)
        if interrupt_clears_buffer is not None:
            clock.interrupt_clears_buffer = interrupt_clears_buffer


def _anchor_ms(clock: Clock, now: float) -> int:
    """Milliseconds of the event this silence is measured from."""
    anchor = clock.last_event_at if clock.last_event_at is not None else clock.turn_started_at
    if anchor is None:
        anchor = now
    return int(anchor * 1000)


def _response_id(event: Mapping[str, Any]) -> str | None:
    response = event.get("response")
    if isinstance(response, Mapping):
        identifier = response.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
    return None


def _silence(clock: Clock, now: float) -> float | None:
    anchor = clock.last_event_at if clock.last_event_at is not None else clock.turn_started_at
    if anchor is None:
        return None
    return max(0.0, now - anchor)


def evaluate(clock: Clock, now: float, settings: StallSettings) -> Decision:
    """Decide mark, recover, and at most one interrupt. Does not mutate."""
    silence = _silence(clock, now)
    # An open question blocks both the mark and the interrupt. A turn that
    # is waiting on a person is not a wedge.
    if clock.elicitation_pending:
        return Decision(seconds_since_last_event=silence)

    running = clock.status in _ACTIVE_STATUSES
    if not running or silence is None or silence < settings.stall_after_s:
        if clock.stalled:
            return Decision(
                emit_recovered=True,
                epoch=clock.stall_epoch,
                anchor_ms=clock.stall_anchor_ms,
                seconds_since_last_event=silence,
            )
        return Decision(seconds_since_last_event=silence)

    emit_stalled = not clock.stalled_emitted
    epoch = clock.stall_epoch + 1 if emit_stalled else clock.stall_epoch
    anchor_ms = _anchor_ms(clock, now)
    cooldown_open = clock.last_auto_interrupt_at is None or (
        now - clock.last_auto_interrupt_at >= settings.cooldown_s
    )
    interrupt = (
        settings.auto_interrupt
        and clock.buffered_count > 0
        and silence >= settings.interrupt_after_s
        and cooldown_open
        and not clock.interrupt_clears_buffer
    )
    return Decision(
        mark_stalled=emit_stalled,
        emit_stalled=emit_stalled,
        interrupt=interrupt,
        buffered_starts_next_turn=interrupt,
        epoch=epoch,
        anchor_ms=anchor_ms,
        seconds_since_last_event=silence,
    )


def _commit(clock: Clock, decision: Decision, now: float) -> None:
    if decision.emit_recovered:
        clock.stalled = False
        clock.stalled_emitted = False
        clock.pending_recovered = False
    if decision.emit_stalled:
        clock.stall_epoch = decision.epoch
        clock.stall_anchor_ms = decision.anchor_ms
        clock.stalled = True
        clock.stalled_emitted = True
    if decision.interrupt:
        # Record the brake before any await so a second tick cannot fire too.
        clock.last_auto_interrupt_at = now


def advance(session_id: str, now: float, settings: StallSettings) -> Decision:
    """Evaluate and commit one session. Side effects stay with the caller."""
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            return Decision()
        decision = evaluate(clock, now, settings)
        _commit(clock, decision, now)
        return decision


def _is_progress(event: Mapping[str, Any]) -> bool:
    event_type = event.get("type")
    return isinstance(event_type, str) and event_type not in NON_PROGRESS_TYPES


def observe_published_event(
    session_id: str,
    event: Mapping[str, Any],
    now: float | None = None,
) -> None:
    """Move the clock for one published event.

    A progress event while the session is stalled queues one recovery.
    Keepalives leave the clock where it is, and do not create one.
    """
    if not _is_progress(event):
        return
    moment = time.time() if now is None else now
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            clock = Clock(status="running", turn_started_at=moment)
            _clocks[session_id] = clock
        clock.last_event_at = moment
        if clock.turn_started_at is None:
            clock.turn_started_at = moment
        event_type = event.get("type")
        if event_type in _TURN_BOUNDARY_TYPES:
            response_id = _response_id(event)
            # A new response id is the next turn. The runner stays
            # running while it drains a buffer into that turn, so this
            # is what clears the buffered count and the turn age.
            if response_id is not None and response_id != clock.turn_response_id:
                clock.turn_response_id = response_id
                clock.buffered_count = 0
                clock.turn_started_at = moment
        if event_type == "session.input.consumed":
            data = event.get("data")
            cleared = data.get("cleared_pending_id") if isinstance(data, Mapping) else None
            # Native queues name the drained pending id here. Accepting
            # a message has no cleared id, so a still-waiting buffer stays.
            if isinstance(cleared, str) and cleared:
                clock.buffered_count = 0
                clock.turn_started_at = moment
        if clock.stalled:
            clock.pending_epoch = clock.stall_epoch
            clock.pending_anchor_ms = clock.stall_anchor_ms
            clock.stalled = False
            clock.stalled_emitted = False
            clock.pending_recovered = True


def drain_pending_publishes() -> list[tuple[str, dict[str, Any]]]:
    """Take queued recovery events and tell the listener once per session."""
    listener = _recovered_listener
    with _lock:
        queued: list[tuple[str, int]] = []
        for session_id in _clocks:
            clock = _clocks.get(session_id)
            if clock is None or not clock.pending_recovered:
                continue
            clock.pending_recovered = False
            queued.append((session_id, clock.pending_anchor_ms))
    events: list[tuple[str, dict[str, Any]]] = []
    for session_id, anchor_ms in queued:
        if listener is not None:
            try:
                listener(session_id, anchor_ms)
            except Exception:
                _logger.exception("stall recovery listener failed for %s", session_id)
        events.append((session_id, {"type": "session.recovered", "conversation_id": session_id}))
    return events


def delivery_report(
    session_id: str,
    *,
    status_before: str | None,
    relaunched: bool,
    now: float | None = None,
) -> dict[str, Any]:
    """Fields for a message ack: delivery state and how long the turn has been quiet.

    A relaunch is reported on its own. A message accepted while a turn is
    already active is buffered behind that turn and counted so the watchdog
    can see it. The age fields describe the clock from before this ack.
    """
    moment = time.time() if now is None else now
    active = status_before in _ACTIVE_STATUSES
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            clock = Clock()
            _clocks[session_id] = clock
        seconds = None if clock.last_event_at is None else max(0.0, moment - clock.last_event_at)
        report: dict[str, Any] = {"seconds_since_last_event": seconds}
        if active:
            if clock.turn_started_at is None:
                report["active_turn_age_s"] = 0.0
            else:
                report["active_turn_age_s"] = max(0.0, moment - clock.turn_started_at)
        if relaunched:
            report["delivery"] = "runner_restarted"
            _start_turn(clock, moment)
        elif active:
            report["delivery"] = "buffered_behind_active_turn"
            clock.buffered_count += 1
            if clock.status not in _ACTIVE_STATUSES:
                clock.status = status_before or "running"
        else:
            report["delivery"] = "turn_started"
            _start_turn(clock, moment)
        return report


def _start_turn(clock: Clock, now: float) -> None:
    clock.status = "running"
    clock.turn_started_at = now
    clock.last_event_at = now
    clock.buffered_count = 0


def snapshot_fields(
    session_id: str,
    *,
    labels: Mapping[str, str] | None = None,
    now: float | None = None,
) -> tuple[bool, float | None]:
    """``(stalled, seconds_since_last_event)`` for a session snapshot.

    A tracked clock wins over the persisted label, so a snapshot taken
    before the label write still matches the watchdog.
    """
    moment = time.time() if now is None else now
    with _lock:
        clock = _clocks.get(session_id)
        if clock is not None:
            seconds = (
                None if clock.last_event_at is None else max(0.0, moment - clock.last_event_at)
            )
            return clock.stalled, seconds
    stalled = bool(labels) and labels.get(STALL_LABEL_KEY) == STALL_LABEL_VALUE
    return stalled, None
