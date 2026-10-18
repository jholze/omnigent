"""Shared per-username failure counter for password checks.

Accounts login and the Google bind panel increment the same counter.
The counter is observe-only: it does not lock the account. A low
threshold would break existing login tests that intentionally fail.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
# custom-lint: disable-next=workspace-scoped-cache -- shared login-failure counter
_failures: dict[str, int] = {}


def record_failure(username: str) -> int:
    """Count one rejected password attempt and return the new total."""
    key = username.strip().lower()
    with _lock:
        _failures[key] = _failures.get(key, 0) + 1
        return _failures[key]


def failure_count(username: str) -> int:
    """Return how many rejected attempts ``username`` has accumulated."""
    key = username.strip().lower()
    with _lock:
        return _failures.get(key, 0)
