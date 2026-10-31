"""Wake an idle parent when a child session returns, whatever created the child.

Tool-registered children already wake the parent from the runner. A child
created through ``POST /v1/sessions`` only records ``session.subagent.returned``
and otherwise leaves an idle parent waiting. This module posts the same
inbox notice through the session events route — so an idle parent starts a
turn, a busy parent buffers it, and an offline host-bound runner is
relaunched — and dedupes that post against the runner's own wake.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import cachetools
from starlette.datastructures import Headers

from omnigent._wrapper_labels import CODEX_NATIVE_WRAPPER_VALUE
from omnigent.db.workspace_cache import WorkspaceScopedCache
from omnigent.entities.conversation import Conversation
from omnigent.runner.subagent_work import (
    SUBAGENT_DISPATCH_ID_LABEL_KEY,
    _format_subagent_wake_notice,
    is_codex_native_subagent_wrapper,
    subagent_wake_message_id,
)
from omnigent.server.routes._auth_helpers import get_session_owner_id
from omnigent.server.schemas import SessionEventInput
from omnigent.stores import ConversationStore

_logger = logging.getLogger(__name__)

# Label written only after a wake post is accepted. A failed forward leaves
# the label unset so the next returned-event retry still delivers.
_WAKE_LABEL_PREFIX = "omnigent.subagent_wake."

Delivery = Callable[[str, SessionEventInput, str | None], Awaitable[dict[str, Any]]]

_app: Any = None
_permission_store: Any = None
_delivery: Delivery | None = None
_delivery_uninstall: Callable[[], None] | None = None
# custom-lint: disable-next=workspace-scoped-cache -- set of Task objects
_tasks: set[asyncio.Task[None]] = set()
# Parent session ids collide across imported workspaces. The conversation
# label is the durable record; these bounds are the in-process fast path.
_WAKE_MEMORY_MAX = 4096
_delivered: WorkspaceScopedCache[tuple[str, str], bool] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_WAKE_MEMORY_MAX)
)
_locks: WorkspaceScopedCache[tuple[str, str], asyncio.Lock] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_WAKE_MEMORY_MAX)
)


@dataclass(frozen=True)
class OwnerWakeContext:
    """Caller identity for a parent wake posted as the session owner.

    Unlike a runner tunnel context, this does not require the session to
    already be bound to a live runner — an offline host must still be able
    to relaunch one.

    :param app: The server application the events route reads state from.
    :param user_id: Owner to attribute the notice to, or ``None`` when
        auth is off.
    :param headers: Empty unless a test supplies some. The wake is not a
        browser request.
    """

    app: Any
    user_id: str | None
    headers: Headers = field(default_factory=Headers)


def install_return_wake_delivery(delivery: Delivery) -> Callable[[], None]:
    """Install the events-route closure that actually posts a parent wake.

    The latest registration wins. Uninstall restores the previous one so
    test routers do not leave a stale closure behind.

    :param delivery: Coroutine ``(parent_id, body, user_id) -> ack``.
    :returns: Callable that restores the prior delivery.
    """
    global _delivery, _delivery_uninstall
    previous = _delivery
    previous_uninstall = _delivery_uninstall

    def uninstall() -> None:
        global _delivery, _delivery_uninstall
        if _delivery is delivery:
            _delivery = previous
            _delivery_uninstall = previous_uninstall

    _delivery = delivery
    _delivery_uninstall = uninstall
    return uninstall


def uninstall_return_wake_delivery() -> None:
    """Pop the events-route delivery installed most recently."""
    if _delivery_uninstall is not None:
        _delivery_uninstall()


def configure_subagent_return_notifier(app: Any, permission_store: Any) -> Callable[[], None]:
    """Enable parent wakes for child completions on this server.

    Installed from the application lifespan, not from every sessions router,
    so tests that only record ``session.subagent.returned`` stay unchanged.

    :param app: Server application whose events route posts the notice.
    :param permission_store: Store used to resolve the parent owner, or
        ``None`` when auth is off.
    :returns: Callable that disables wakes and cancels any still in flight.
    """
    global _app, _permission_store
    previous_app = _app
    previous_store = _permission_store
    _app = app
    _permission_store = permission_store

    def uninstall() -> None:
        global _app, _permission_store
        _app = previous_app
        _permission_store = previous_store
        _delivered.clear()
        _locks.clear()
        for task in list(_tasks):
            task.cancel()

    return uninstall


def reset_subagent_return_wakes_for_tests() -> None:
    """Drop in-memory dedupe state. Tests own their conversation rows."""
    _delivered.clear()
    _locks.clear()


def schedule_subagent_return_wake(
    *,
    child: Conversation,
    parent_id: str,
    store: ConversationStore,
    turn_key: str,
    status: str,
) -> None:
    """Queue a parent wake for one returned child, if this process is serving.

    A duplicate ``session.subagent.returned`` still schedules: the first
    attempt may have failed before the delivery label was written. The
    label (and the in-memory set) collapse a second successful post.

    :param child: Child conversation that just returned.
    :param parent_id: Parent session to wake.
    :param store: Store used to read the parent and stamp the delivery label.
    :param turn_key: Child turn id captured before any native request-id
        rewrite, e.g. a response id or the child id when the turn produced
        no transcript.
    :param status: Terminal status, e.g. ``"completed"`` or ``"failed"``.
    """
    if _app is None or _delivery is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        _wake_parent(
            child_id=child.id,
            parent_id=parent_id,
            store=store,
            turn_key=turn_key,
            status=status,
        ),
        name=f"subagent-return-wake-{child.id}",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def flush_subagent_return_wakes() -> None:
    """Wait for wakes scheduled on this loop. Tests use this instead of sleeping."""
    pending = [task for task in list(_tasks) if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def post_deduped_subagent_wake(
    parent_id: str,
    body: SessionEventInput,
    post: Callable[[SessionEventInput], Awaitable[dict[str, Any]]],
    store: ConversationStore,
) -> dict[str, Any]:
    """Forward one wake notice once per child turn.

    The runner and this server both post when a tool-registered child
    finishes. They share :func:`subagent_wake_message_id`, and this holds
    that key's lock across the forward so only the first accepted post
    is delivered. A deny is not marked, so a later attempt can still land.

    :param parent_id: Parent session receiving the notice.
    :param body: Message event carrying ``subagent_wake_key``.
    :param post: Events-route implementation with the key already stripped.
    :param store: Store whose labels remember a successful delivery.
    :returns: The events-route acknowledgement, or a deduplicated ack when
        this turn was already delivered.
    """
    wake_key = body.subagent_wake_key
    if not wake_key:
        return await post(body)
    lock = _locks.setdefault((parent_id, wake_key), asyncio.Lock())
    async with lock:
        if await _already_delivered(store, parent_id, wake_key):
            return {"queued": True, "deduplicated": True}
        result = await post(body.model_copy(update={"subagent_wake_key": None}))
        if result.get("queued") is True and result.get("denied") is not True:
            await _mark_delivered(store, parent_id, wake_key)
        return result


def _label_key(wake_key: str) -> str:
    return f"{_WAKE_LABEL_PREFIX}{wake_key}"


def _resolve_turn_key(child: Conversation, turn_key: str) -> str:
    """Prefer a real turn id, then the dispatch id both sides already share."""
    if turn_key and turn_key != child.id:
        return turn_key
    dispatch_id = child.labels.get(SUBAGENT_DISPATCH_ID_LABEL_KEY)
    if isinstance(dispatch_id, str) and dispatch_id:
        return dispatch_id
    return turn_key or child.id


def _skips_return_wake(child: Conversation, parent: Conversation) -> bool:
    """Codex consumes child threads itself; a wake would inject into that chat."""
    wrapper = child.labels.get("omnigent.wrapper")
    if is_codex_native_subagent_wrapper(wrapper if isinstance(wrapper, str) else None):
        return True
    if parent.labels.get("omnigent.wrapper") == CODEX_NATIVE_WRAPPER_VALUE:
        return True
    return parent.harness_override == "codex-native"


async def _already_delivered(store: ConversationStore, parent_id: str, wake_key: str) -> bool:
    if (parent_id, wake_key) in _delivered:
        return True
    parent = await asyncio.to_thread(store.get_conversation, parent_id)
    if parent is None:
        return False
    return parent.labels.get(_label_key(wake_key)) == "1"


async def _mark_delivered(store: ConversationStore, parent_id: str, wake_key: str) -> None:
    _delivered[(parent_id, wake_key)] = True
    try:
        await asyncio.to_thread(store.set_labels, parent_id, {_label_key(wake_key): "1"})
    except Exception:  # marker write must not fail a delivered wake
        _logger.warning(
            "Could not persist subagent wake marker for parent=%s",
            parent_id,
            exc_info=True,
        )


async def _wake_parent(
    *,
    child_id: str,
    parent_id: str,
    store: ConversationStore,
    turn_key: str,
    status: str,
) -> None:
    delivery = _delivery
    app = _app
    if delivery is None or app is None:
        return
    try:
        child = await asyncio.to_thread(store.get_conversation, child_id)
        parent = await asyncio.to_thread(store.get_conversation, parent_id)
        if child is None or parent is None or parent.id != parent_id:
            return
        if _skips_return_wake(child, parent):
            return
        user_id: str | None = None
        if _permission_store is not None:
            user_id = await asyncio.to_thread(get_session_owner_id, parent_id, _permission_store)
            if user_id is None:
                _logger.info(
                    "Skipping subagent return wake; parent %s has no owner",
                    parent_id,
                )
                return
        resolved = _resolve_turn_key(child, turn_key)
        wake_key = subagent_wake_message_id(child.id, resolved)
        from omnigent.server.subagent_activity import _title

        notice = _format_subagent_wake_notice(
            agent=child.sub_agent_name or "sub-agent",
            title=_title(child),
            status=status,
            pending=1,
        )
        body = SessionEventInput(
            type="message",
            subagent_wake_key=wake_key,
            data={
                "role": "user",
                "stable_id": wake_key,
                "content": [{"type": "input_text", "text": notice}],
            },
        )
        await delivery(parent_id, body, user_id)
    except Exception:  # parent wake must not fail child completion
        _logger.warning(
            "Subagent return wake failed for parent=%s child=%s",
            parent_id,
            child_id,
            exc_info=True,
        )
