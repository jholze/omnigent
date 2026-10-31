"""Idempotent session-event POST and a shared retry loop.

A client that times out after the server has stored the item retries with
the same ``Idempotency-Key``. The server returns the stored acknowledgement
and does not append a second item. Retries cover transport failures only;
an HTTP 4xx is the caller's to handle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import cachetools

from omnigent.db.workspace_cache import WorkspaceScopedCache

_ACK_MAX = 4096
_BACKOFF_S = (0.05, 0.2, 0.8)

T = TypeVar("T")

_acks: WorkspaceScopedCache[tuple[str, str], Any] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_ACK_MAX)
)
_locks: WorkspaceScopedCache[tuple[str, str], asyncio.Lock] = WorkspaceScopedCache(
    lambda: cachetools.LRUCache(maxsize=_ACK_MAX)
)


def reset_for_tests() -> None:
    """Drop remembered acknowledgements and in-flight locks."""
    _acks.clear()
    _locks.clear()


async def resolve_idempotent(
    session_id: str,
    key: str,
    producer: Callable[[], Awaitable[T]],
) -> T:
    """Return the stored ack for ``key``, or run ``producer`` once and store it.

    The producer is not called again when it completed. A producer that
    raises is not stored, so a later retry can try the work again.
    An empty key skips the cache.
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
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        result = await producer()
        _acks[slot] = result
        return result


async def retry_same_key(
    operation: Callable[[str], Awaitable[T]],
    *,
    key: str,
    max_attempts: int = 3,
    backoff_s: tuple[float, ...] = _BACKOFF_S,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (TimeoutError,),
) -> T:
    """Call ``operation`` with ``key`` up to ``max_attempts`` times.

    Each attempt receives the same key. Only ``retry_exceptions`` are
    retried. The last such error is re-raised.
    """
    sleeper = sleep or asyncio.sleep
    attempts = max(1, max_attempts)
    last: BaseException | None = None
    for index in range(attempts):
        try:
            return await operation(key)
        except retry_exceptions as exc:
            last = exc
            if index + 1 >= attempts:
                raise
            delay = backoff_s[min(index, len(backoff_s) - 1)] if backoff_s else 0.0
            if delay:
                await sleeper(delay)
    assert last is not None
    raise last
