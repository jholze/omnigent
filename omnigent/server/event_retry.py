"""Retry one idempotent call without importing the database.

The Python client imports this on host and runner startup. Those processes
must not load ``omnigent.db``. The server cache that dedupes the stored
item stays in :mod:`omnigent.server.event_idempotency`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

_BACKOFF_S = (0.05, 0.2, 0.8)

T = TypeVar("T")


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
