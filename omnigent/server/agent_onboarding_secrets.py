"""Detect token-like strings in onboarding answers and findings.

A hit rejects the write. The matched text is never returned, logged, or stored.
"""

from __future__ import annotations

import math
import re

_TOKEN_PATTERNS = (
    re.compile(r"ogc_[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]+-----"),
)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{40,}")
_ENTROPY_MIN = 4.5


def contains_secret(value: object) -> bool:
    """Whether *value* contains a token-like secret.

    Hex digests of length 64 score about 4 bits and are not treated as secrets.
    PEM blocks, ``ogc_`` tokens, and high-entropy blobs are.
    """
    return _scan(_flatten(value))


def _flatten(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in (*value.keys(), *value.values()))
    if isinstance(value, list | tuple):
        return " ".join(_flatten(item) for item in value)
    return ""


def _scan(text: str) -> bool:
    if not text:
        return False
    if any(pattern.search(text) for pattern in _TOKEN_PATTERNS):
        return True
    return any(_shannon(token) >= _ENTROPY_MIN for token in _LONG_TOKEN.findall(text))


def _shannon(value: str) -> float:
    length = len(value)
    if length == 0:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = float(length)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())
