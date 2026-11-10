"""Watch running turns and recover the ones that go quiet.

A turn that emits no progress for ``OMNIGENT_STALL_AFTER_S`` (default 300)
is marked stalled. When input is also buffered and the silence reaches
``OMNIGENT_STALL_INTERRUPT_AFTER_S`` (default 600), the server interrupts
that turn once through the existing interrupt path so the buffered message
can start the next one. An open elicitation is left alone. At most one
automatic interrupt is allowed per session per 30 minutes.

``OMNIGENT_STALL_AUTO_INTERRUPT=0`` keeps the mark and the event and skips
the interrupt. Nothing here reads the chat feature flag.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from omnigent.runtime import pending_elicitations, pending_inputs, session_stream, stall_progress
from omnigent.runtime.stall_progress import Decision, StallSettings
from omnigent.server import session_outbox
from omnigent.server.auth import env_var_is_truthy

_logger = logging.getLogger(__name__)

_TICK_S = 5.0
_DEFAULT_STALL_AFTER_S = 300.0
_DEFAULT_INTERRUPT_AFTER_S = 600.0
_COOLDOWN_S = 1800.0

LabelWriter = Callable[[str, str | None], None]
Publisher = Callable[[str, dict[str, Any]], int]
InterruptCallback = Callable[[str], Awaitable[None] | None]

_label_writer: LabelWriter | None = None
_publisher: Publisher = session_stream.publish
_interrupt: InterruptCallback | None = None
_guard = threading.Lock()
# One tick per event loop. A nested TestClient has its own portal loop,
# and awaiting the other loop's task raises and cancels that tick.
# custom-lint: disable-next=workspace-scoped-cache -- per-loop task, not tenant data
_by_loop: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event, asyncio.Task[None]]] = {}


def load_settings() -> StallSettings:
    """Read the stall thresholds from the environment."""
    return StallSettings(
        stall_after_s=_float_env("OMNIGENT_STALL_AFTER_S", _DEFAULT_STALL_AFTER_S),
        interrupt_after_s=_float_env(
            "OMNIGENT_STALL_INTERRUPT_AFTER_S", _DEFAULT_INTERRUPT_AFTER_S
        ),
        auto_interrupt=env_var_is_truthy("OMNIGENT_STALL_AUTO_INTERRUPT", default=True),
        cooldown_s=_COOLDOWN_S,
    )


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def install_label_writer(writer: LabelWriter | None) -> Callable[[], None]:
    """Install the sync label writer. The returned callable restores the previous one."""
    global _label_writer
    previous = _label_writer
    _label_writer = writer

    def uninstall() -> None:
        global _label_writer
        if _label_writer is writer:
            _label_writer = previous

    return uninstall


def install_publisher(publisher: Publisher) -> Callable[[], None]:
    """Install the live-stream publisher used for stall events."""
    global _publisher
    previous = _publisher
    _publisher = publisher

    def uninstall() -> None:
        global _publisher
        if _publisher is publisher:
            _publisher = previous

    return uninstall


def register_interrupt_callback(callback: InterruptCallback) -> Callable[[], None]:
    """Register the events-route interrupt used by an automatic recovery."""
    global _interrupt
    previous = _interrupt
    _interrupt = callback

    def uninstall() -> None:
        global _interrupt
        if _interrupt is callback:
            _interrupt = previous

    return uninstall


def uninstall_interrupt_callback() -> None:
    """Drop the events-route interrupt callback."""
    global _interrupt
    _interrupt = None


def reset_for_tests() -> None:
    """Clear clocks. Callbacks installed by the server stay in place."""
    stall_progress.reset_for_tests()


def annotate_message_ack(
    session_id: str,
    ack: dict[str, Any],
    *,
    status_before: str | None,
    relaunched: bool,
    now: float | None = None,
) -> dict[str, Any]:
    """Add delivery fields to a successful message acknowledgement."""
    ack.update(
        stall_progress.delivery_report(
            session_id,
            status_before=status_before,
            relaunched=relaunched,
            now=now,
        )
    )
    return ack


def _write_label(session_id: str, value: str | None) -> None:
    writer = _label_writer
    if writer is None:
        return
    try:
        writer(session_id, value)
    except Exception:
        _logger.exception("stall label write failed for %s", session_id)


def _on_recovered(session_id: str, anchor_ms: int) -> None:
    """Persist a recovery that a progress event just queued."""
    _write_label(session_id, None)
    try:
        session_outbox.record_session_recovered(session_id=session_id, anchor_ms=anchor_ms)
    except Exception:
        _logger.exception("session.recovered outbox write failed for %s", session_id)


def _publish(session_id: str, event: dict[str, Any]) -> None:
    try:
        _publisher(session_id, event)
    except Exception:
        _logger.exception("stall event publish failed for %s", session_id)


async def apply_effects(session_id: str, decision: Decision) -> None:
    """Publish, label, record, and maybe interrupt for one decision."""
    if decision.emit_stalled:
        clock = stall_progress.get_clock(session_id)
        # A progress event can clear the flag before this side effect runs.
        if clock is not None and clock.stalled:
            _write_label(session_id, stall_progress.STALL_LABEL_VALUE)
            _publish(
                session_id,
                {
                    "type": "session.stalled",
                    "conversation_id": session_id,
                    "seconds_since_last_event": decision.seconds_since_last_event,
                },
            )
            try:
                session_outbox.record_session_stalled(
                    session_id=session_id,
                    anchor_ms=decision.anchor_ms,
                    seconds_since_last_event=decision.seconds_since_last_event,
                )
            except Exception:
                _logger.exception("session.stalled outbox write failed for %s", session_id)
    if decision.emit_recovered:
        _write_label(session_id, None)
        _publish(
            session_id,
            {"type": "session.recovered", "conversation_id": session_id},
        )
        try:
            session_outbox.record_session_recovered(
                session_id=session_id, anchor_ms=decision.anchor_ms
            )
        except Exception:
            _logger.exception("session.recovered outbox write failed for %s", session_id)
    if decision.interrupt:
        callback = _interrupt
        if callback is None:
            return
        result = callback(session_id)
        if result is not None:
            await result


def _raw_status(session_id: str) -> str | None:
    from omnigent.server.routes._sessions.helpers import _session_status_cache

    value = _session_status_cache.get(session_id)
    return value if isinstance(value, str) else None


def _seed_running(now: float) -> None:
    from omnigent.server.routes._sessions.helpers import _session_status_cache

    for session_id in _session_status_cache:
        status = _session_status_cache.get(session_id)
        # A waiting parent has no active turn. Seeding it would start
        # a stall clock for an orchestrator that is only waiting on children.
        if status == "running":
            stall_progress.note_running(session_id, now)


_INTERRUPT_CLEARS_BUFFER = frozenset({"claude-native"})


def _harness_interrupt_clears_buffer(session_id: str) -> bool | None:
    """Whether Stop on this session's harness drops the runner message buffer.

    claude-native's interrupt path pops ``_session_message_buffers``. An
    automatic stall interrupt would delete the queued follow-up instead of
    letting it start the next turn (FR-3). Codex-native and SDK harnesses
    keep the buffer. ``None`` means the harness is unknown; leave the clock.
    """
    from omnigent.harness_aliases import canonicalize_harness
    from omnigent.runtime._globals import _conversation_store
    from omnigent.server.routes._sessions.helpers import _resolve_harness
    from omnigent.util.uuid import InvalidUuidError

    if _conversation_store is None:
        return None
    try:
        conv = _conversation_store.get_conversation(session_id)
    except InvalidUuidError:
        return None
    except Exception as exc:
        # SQLAlchemy wraps the uuid error in StatementError. A bad key is
        # still an unknown session, not a failed tick.
        if not isinstance(exc.__cause__, InvalidUuidError):
            raise
        return None
    if conv is None:
        return None
    harness = _resolve_harness(conv)
    if not isinstance(harness, str) or not harness:
        return None
    return (canonicalize_harness(harness) or harness) in _INTERRUPT_CLEARS_BUFFER


def _refresh(session_id: str) -> None:
    pending_count = len(pending_inputs.pending_ids(session_id))
    stall_progress.update_runtime_facts(
        session_id,
        status=_raw_status(session_id),
        elicitation_pending=pending_elicitations.count_for(session_id) > 0,
        pending_input_count=pending_count,
        interrupt_clears_buffer=_harness_interrupt_clears_buffer(session_id),
    )


async def evaluate_session(
    session_id: str,
    now: float | None = None,
    settings: StallSettings | None = None,
) -> Decision:
    """Refresh one session, advance its clock, and apply side effects."""
    moment = time.time() if now is None else now
    chosen = settings if settings is not None else load_settings()
    _refresh(session_id)
    decision = stall_progress.advance(session_id, moment, chosen)
    await apply_effects(session_id, decision)
    return decision


async def tick(now: float | None = None) -> None:
    """Evaluate every tracked session once."""
    moment = time.time() if now is None else now
    _seed_running(moment)
    settings = load_settings()
    for session_id in stall_progress.session_ids():
        try:
            _refresh(session_id)
            decision = stall_progress.advance(session_id, moment, settings)
            await apply_effects(session_id, decision)
        except Exception:
            _logger.exception("stall watchdog failed for %s", session_id)


async def _loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await tick()
        except Exception:
            _logger.exception("stall watchdog tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=_TICK_S)


def _drop_loop_task(loop_id: int, task: asyncio.Task[None]) -> None:
    with _guard:
        current = _by_loop.get(loop_id)
        if current is not None and current[2] is task:
            _by_loop.pop(loop_id, None)


def start_stall_watchdog() -> asyncio.Task[None]:
    """Start the background tick on this event loop.

    A second call on the same loop returns the running task. A call on
    another loop starts its own tick and leaves the first one running.
    """
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    with _guard:
        current = _by_loop.get(loop_id)
        if current is not None and current[0] is loop and not current[2].done():
            return current[2]
        stop = asyncio.Event()
        task = asyncio.create_task(_loop(stop), name="stall-watchdog")
        _by_loop[loop_id] = (loop, stop, task)
    task.add_done_callback(lambda finished, loop_id=loop_id: _drop_loop_task(loop_id, finished))
    return task


async def stop_stall_watchdog() -> None:
    """Cancel the tick that belongs to this event loop."""
    loop = asyncio.get_running_loop()
    with _guard:
        current = _by_loop.get(id(loop))
        if current is None or current[0] is not loop:
            return
        _by_loop.pop(id(loop), None)
        stop, task = current[1], current[2]
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


stall_progress.set_recovered_listener(_on_recovered)
