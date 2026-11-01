"""Idempotent session-event POST.

The retry loop lives in :mod:`omnigent.server.event_retry` so the Python
client can import it without loading the database. This module re-exports
:func:`~omnigent.server.event_retry.retry_same_key`.

A client that times out after the server has stored the item retries with
the same ``Idempotency-Key``. The server returns the stored acknowledgement
and does not append a second item. Retries cover transport failures only;
an HTTP 4xx is the caller's to handle.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import cachetools

from omnigent.db.workspace_cache import WorkspaceScopedCache
from omnigent.server.event_retry import retry_same_key as retry_same_key

_ACK_MAX = 4096
T = TypeVar("T")

_acks: WorkspaceScopedCache[tuple[str, str], Any] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_ACK_MAX)
)
_locks: WorkspaceScopedCache[tuple[str, str], asyncio.Lock] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_ACK_MAX)
)


class IdempotencyConflict(Exception):
    """This key was already stored for a different request body."""


class _Ack:
    """One stored acknowledgement and the body it belongs to."""

    __slots__ = ("fingerprint", "value")

    def __init__(self, fingerprint: str | None, value: Any) -> None:
        self.fingerprint = fingerprint
        self.value = value


def event_fingerprint(payload: Any) -> str:
    """Stable hash of a JSON-shaped event body."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reset_for_tests() -> None:
    """Drop remembered acknowledgements and in-flight locks."""
    _acks.clear()
    _locks.clear()


async def resolve_idempotent(
    session_id: str,
    key: str,
    producer: Callable[[], Awaitable[T]],
    *,
    fingerprint: str | None = None,
) -> T:
    """Return the stored ack for ``key``, or run ``producer`` once and store it.

    The producer is not called again when it completed. A producer that
    raises is not stored, so a later retry can try the work again.
    An empty key skips the cache. The same key with a different
    ``fingerprint`` raises :class:`IdempotencyConflict` and does not
    run ``producer``.
    """
    cleaned = key.strip()
    if not cleaned:
        return await producer()
    slot = (session_id, cleaned)
    lock = _locks.get(slot)
    if lock is None:
        lock = asyncio.Lock()
        # Two tasks can both miss. setdefault keeps the first lock.
        lock = _locks.setdefault(slot, lock)
    async with lock:
        cached = _acks.get(slot)
        if isinstance(cached, _Ack):
            if fingerprint is not None and cached.fingerprint != fingerprint:
                raise IdempotencyConflict(session_id)
            return cached.value  # type: ignore[no-any-return]
        result = await producer()
        _acks[slot] = _Ack(fingerprint, result)
        return result
