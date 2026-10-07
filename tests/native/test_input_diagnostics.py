"""Input correlation stays isolated and cannot expose message content."""

import asyncio
import logging

import pytest

from omnigent.native.input_diagnostics import (
    current_input_attributes,
    input_attributes,
    input_delivery_scope,
    log_input_event,
)


def test_input_attributes_reject_content_and_malformed_identifiers() -> None:
    assert input_attributes(
        {
            "input_stable_id": "a" * 32,
            "pending_id": "pending_" + "b" * 32,
            "delivery_attempt_id": "private prompt",
            "input_enqueued_at_ms": True,
            "content": "private prompt",
            "created_by": "person@example.com",
            "response_id": "untrusted response",
        }
    ) == {"input_stable_id": "a" * 32, "pending_id": "pending_" + "b" * 32}
    assert input_attributes({"input_stable_id": {"text": "private prompt"}}) == {}


@pytest.mark.asyncio
async def test_concurrent_inputs_and_workers_keep_their_own_identity() -> None:
    async def observe(letter: str) -> dict[str, object]:
        with input_delivery_scope({"input_stable_id": letter * 32}, response_id="resp_" + letter):
            await asyncio.sleep(0)
            observed = await asyncio.to_thread(current_input_attributes)
            with input_delivery_scope(None):
                assert current_input_attributes() == {}
            assert current_input_attributes() == observed
            return observed

    assert await asyncio.gather(observe("a"), observe("b")) == [
        {"input_stable_id": "a" * 32, "response_id": "resp_a"},
        {"input_stable_id": "b" * 32, "response_id": "resp_b"},
    ]
    assert current_input_attributes() == {}


def test_logging_failure_cannot_interrupt_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = logging.getLogger(__name__)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unavailable log sink")

    monkeypatch.setattr(logger, "info", fail)
    with input_delivery_scope({"input_stable_id": "a" * 32}):
        log_input_event(logger, "native_input_execution_finished", outcome="executor_returned")
    assert current_input_attributes() == {}
