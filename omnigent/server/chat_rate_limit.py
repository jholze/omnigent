"""In-process sliding windows for team chat and agent-account MCP calls.

Agent accounts share one 120-request window across ``/mcp`` and
``/v1/chat/*``. An in-process call from ``/mcp`` into ``/v1/chat`` is not
counted again. Posts have a separate 30-per-minute window per principal
and channel. Limits are per process.
"""

from __future__ import annotations

import math
import os
import threading
import time

_WINDOW_SECONDS = 60.0
_DEFAULT_AGENT_RPM = 120
_DEFAULT_POST_RPM = 30
_AGENT_RPM_ENV = "OMNIGENT_AGENT_MCP_RPM"
_POST_RPM_ENV = "OMNIGENT_AGENT_CHAT_POST_RPM"
_QUOTA_ENV = "OMNIGENT_BOT_SESSION_QUOTA"
_QUOTA_WINDOW_US = 24 * 60 * 60 * 1_000_000


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return default


def agent_requests_per_minute() -> int:
    """Accepted ``/mcp`` plus ``/v1/chat`` requests per agent account per minute."""
    return _positive_int_env(_AGENT_RPM_ENV, _DEFAULT_AGENT_RPM)


def channel_posts_per_minute() -> int:
    """Accepted posts per principal per channel per minute."""
    return _positive_int_env(_POST_RPM_ENV, _DEFAULT_POST_RPM)


class _SlidingWindow:
    """Per-key sliding window that reports how long the caller should wait."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, now: float, window: float) -> int | None:
        """Record a hit, or return retry-after seconds when *key* is over *limit*.

        A rejection does not record another hit. The hint is clamped to 1–60
        seconds, matching the window length.
        """
        cutoff = now - window
        with self._lock:
            hits = [stamp for stamp in self._hits.get(key, ()) if stamp > cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                remaining = hits[0] + window - now
                retry = math.ceil(remaining) if remaining > 0 else 1
                return min(60, max(1, int(retry)))
            hits.append(now)
            self._hits[key] = hits
            return None

    def clear(self) -> None:
        """Drop every key. Tests use this so windows do not leak across cases."""
        with self._lock:
            self._hits.clear()


_agent_window = _SlidingWindow()
_post_window = _SlidingWindow()
_quota_lock = threading.Lock()
# custom-lint: disable-next=workspace-scoped-cache -- keyed by agent-account UUID
_mcp_session_starts: dict[str, list[int]] = {}


def bot_session_quota() -> int | None:
    """Configured sessions per agent account per 24 h, or ``None`` when off.

    Unset, blank, and non-positive values leave the quota disabled. Runs and
    concurrent sessions use this same cap until a separate limit is chosen.
    """
    raw = os.environ.get(_QUOTA_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def quota_window_start_us(now_us: int) -> int:
    """Start of the 24 h quota window ending at *now_us*."""
    return now_us - _QUOTA_WINDOW_US


def note_bot_session_start(account_id: str, *, at_us: int) -> None:
    """Count one MCP session create that is not a chat-thread row."""
    with _quota_lock:
        hits = _mcp_session_starts.setdefault(account_id, [])
        hits.append(at_us)


def mcp_session_starts_since(account_id: str, since_us: int) -> int:
    """In-memory MCP creates for *account_id* at or after *since_us*."""
    with _quota_lock:
        hits = [stamp for stamp in _mcp_session_starts.get(account_id, ()) if stamp >= since_us]
        if hits:
            _mcp_session_starts[account_id] = hits
        else:
            _mcp_session_starts.pop(account_id, None)
        return len(hits)


def bot_session_quota_exceeded(account_id: str, persisted: int, *, now_us: int) -> bool:
    """Whether another session start would pass the configured cap."""
    limit = bot_session_quota()
    if limit is None:
        return False
    since = quota_window_start_us(now_us)
    return persisted + mcp_session_starts_since(account_id, since) >= limit


def consume_agent_request(principal_id: str, *, now: float | None = None) -> int | None:
    """Consume one combined agent-account request.

    :returns: ``None`` when the call is allowed, otherwise retry-after seconds.
    """
    return _agent_window.consume(
        principal_id,
        limit=agent_requests_per_minute(),
        now=time.time() if now is None else now,
        window=_WINDOW_SECONDS,
    )


def consume_channel_post(
    principal_id: str, channel_id: str, *, now: float | None = None
) -> int | None:
    """Consume one post attempt in *channel_id*.

    :returns: ``None`` when the post is allowed, otherwise retry-after seconds.
    """
    return _post_window.consume(
        f"{principal_id}\n{channel_id}",
        limit=channel_posts_per_minute(),
        now=time.time() if now is None else now,
        window=_WINDOW_SECONDS,
    )


def reset_chat_rate_limits() -> None:
    """Forget recorded hits. Test-only."""
    _agent_window.clear()
    _post_window.clear()
    with _quota_lock:
        _mcp_session_starts.clear()
