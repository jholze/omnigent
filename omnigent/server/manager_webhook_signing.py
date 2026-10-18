"""HMAC-SHA256 request signing for manager-webhook delivery.

Stripe-style scheme: sign ``{timestamp}.{event_id}.{raw_json_body}`` and
send the digest as ``X-Omnigent-Signature: v1=<hex>``. The signing key is
read from the environment, never from the config file.

The server signs with :func:`current_signing_key` and :func:`sign` only.
:func:`previous_secret` and :func:`verify` are receiver-side helpers: a
receiver, and the tests, use them to accept the current key and, during
rotation, the previous key. Delivery never signs with the previous key.

The signed-content format matches the upstream durable session-lifecycle
push so a receiver can later switch to that implementation unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import os

SECRET_ENV_VAR = "OMNIGENT_MANAGER_WEBHOOK_SECRET"
PREVIOUS_SECRET_ENV_VAR = "OMNIGENT_MANAGER_WEBHOOK_SECRET_PREVIOUS"

DEFAULT_TOLERANCE_SECONDS = 300


def current_signing_key() -> str | None:
    """The active signing key, or ``None`` when the env var is unset."""
    value = os.environ.get(SECRET_ENV_VAR)
    return value if value else None


def previous_secret() -> str | None:
    """The previous key during a rotation window, or ``None``."""
    value = os.environ.get(PREVIOUS_SECRET_ENV_VAR)
    return value if value else None


def canonicalize(*, timestamp: int, event_id: str, raw_json_body: str) -> str:
    """Build ``{timestamp}.{event_id}.{raw_json_body}``."""
    return f"{timestamp}.{event_id}.{raw_json_body}"


def sign(*, secret: str, timestamp: int, event_id: str, raw_json_body: str) -> str:
    """Return the ``X-Omnigent-Signature`` value ``v1=<hex hmac-sha256>``.

    :param secret: Current signing key. The previous key never signs.
    :param timestamp: Unix epoch seconds at signing time.
    :param event_id: Stable event id.
    :param raw_json_body: Exact body bytes, as a str, that will be sent.
    """
    signed_content = canonicalize(
        timestamp=timestamp, event_id=event_id, raw_json_body=raw_json_body
    )
    digest = hmac.new(
        secret.encode("utf-8"), signed_content.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"v1={digest}"


def verify(
    *,
    signature_header: str,
    timestamp: int,
    event_id: str,
    raw_json_body: str,
    now: int,
    secrets: list[str],
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Return true when the timestamp is fresh and the signature matches.

    A stale timestamp is rejected even with a valid signature. Empty secret
    entries are ignored. Comparison is constant-time per candidate.
    """
    if abs(now - timestamp) > tolerance_seconds:
        return False
    for secret in secrets:
        if not secret:
            continue
        expected = sign(
            secret=secret,
            timestamp=timestamp,
            event_id=event_id,
            raw_json_body=raw_json_body,
        )
        try:
            matches = hmac.compare_digest(signature_header, expected)
        except (TypeError, ValueError):
            return False
        if matches:
            return True
    return False


def build_headers(
    *,
    event_id: str,
    event_type: str,
    attempt: int,
    timestamp: int,
    key_id: str | None,
    signature: str,
) -> dict[str, str]:
    """Header set for one delivery attempt."""
    headers = {
        "Content-Type": "application/json",
        "X-Omnigent-Event-Id": event_id,
        "X-Omnigent-Event-Type": event_type,
        "X-Omnigent-Delivery-Attempt": str(attempt),
        "X-Omnigent-Timestamp": str(timestamp),
        "X-Omnigent-Signature": signature,
    }
    if key_id:
        headers["X-Omnigent-Key-Id"] = key_id
    return headers
