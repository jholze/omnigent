"""Content-free correlation for web messages delivered to native harnesses."""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterator, Mapping
from contextvars import ContextVar

_IDENTIFIERS = {
    "input_stable_id": re.compile(r"[0-9a-f]{32}"),
    "pending_id": re.compile(r"pending_[0-9a-f]{32}"),
    "delivery_attempt_id": re.compile(r"[0-9a-f]{32}"),
}
INPUT_FIELDS = frozenset((*_IDENTIFIERS, "input_enqueued_at_ms"))
_input_context: ContextVar[dict[str, object] | None] = ContextVar(
    "native_input_delivery", default=None
)


def input_attributes(source: Mapping[str, object] | None) -> dict[str, str | int]:
    """Keep only server-generated IDs and the server's enqueue timestamp."""
    attrs: dict[str, str | int] = {}
    if source is None:
        return attrs
    for key, pattern in _IDENTIFIERS.items():
        value = source.get(key)
        if isinstance(value, str) and pattern.fullmatch(value):
            attrs[key] = value
    enqueued_at = source.get("input_enqueued_at_ms")
    if (
        isinstance(enqueued_at, int)
        and not isinstance(enqueued_at, bool)
        and 0 < enqueued_at < 10**15
    ):
        attrs["input_enqueued_at_ms"] = enqueued_at
    return attrs


@contextlib.contextmanager
def input_delivery_scope(
    source: Mapping[str, object] | None, *, response_id: str | None = None
) -> Iterator[None]:
    """Bind one input, including across to_thread, without inheriting another input's IDs."""
    attrs: dict[str, object] = dict(input_attributes(source))
    if attrs and response_id is not None:
        attrs["response_id"] = response_id
    token = _input_context.set(attrs)
    try:
        yield
    finally:
        _input_context.reset(token)


def current_input_attributes() -> dict[str, object]:
    """Return a copy so one diagnostic cannot change another's correlation."""
    return dict(_input_context.get() or {})


def with_input_attributes(
    extra: dict[str, object], source: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Add input identity to an existing structured log without replacing its other fields."""
    existing = extra.get("attributes")
    extra["attributes"] = {
        **(existing if isinstance(existing, dict) else {}),
        **(current_input_attributes() if source is None else input_attributes(source)),
    }
    return extra


def log_input_event(
    logger: logging.Logger,
    event_name: str,
    *,
    session_id: str | None = None,
    attributes: Mapping[str, object] | None = None,
    **fields: object,
) -> None:
    """Emit caller-selected identifiers and bounded outcomes without affecting delivery."""
    with contextlib.suppress(Exception):
        # Hooks import native modules at startup; load the logging sink only on use.
        from omnigent.debug_logging import debug_event

        correlation = current_input_attributes() if attributes is None else dict(attributes)
        if attributes is None and not correlation:
            return
        extra = debug_event(event_name, session_id=session_id)
        extra["attributes"] = {**correlation, **fields}
        logger.info("%s", event_name, extra=extra)
