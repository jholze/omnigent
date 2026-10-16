"""HMAC signing for the manager webhook matches the upstream v1 scheme."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from omnigent.server import manager_webhook_signing as signing


def test_sign_and_verify_round_trip() -> None:
    body = '{"event_id":"abc","event_type":"session.completed"}'
    signature = signing.sign(
        secret="test-key", timestamp=1_700_000_000, event_id="abc", raw_json_body=body
    )
    assert signature.startswith("v1=")
    digest = signature.removeprefix("v1=")
    signed = signing.canonicalize(timestamp=1_700_000_000, event_id="abc", raw_json_body=body)
    assert digest == hmac.new(b"test-key", signed.encode(), hashlib.sha256).hexdigest()
    assert signing.verify(
        signature_header=signature,
        timestamp=1_700_000_000,
        event_id="abc",
        raw_json_body=body,
        now=1_700_000_100,
        secrets=["test-key"],
    )


def test_previous_key_verifies_and_does_not_sign() -> None:
    body = "{}"
    signature = signing.sign(secret="current", timestamp=100, event_id="evt", raw_json_body=body)
    assert signing.verify(
        signature_header=signature,
        timestamp=100,
        event_id="evt",
        raw_json_body=body,
        now=100,
        secrets=["current", "previous"],
    )
    previous_signature = signing.sign(
        secret="previous", timestamp=100, event_id="evt", raw_json_body=body
    )
    assert signing.verify(
        signature_header=previous_signature,
        timestamp=100,
        event_id="evt",
        raw_json_body=body,
        now=100,
        secrets=["current", "previous"],
    )
    assert signature != previous_signature


def test_verify_rejects_stale_tampered_and_empty_keys() -> None:
    body = '{"ok":true}'
    signature = signing.sign(secret="k", timestamp=500, event_id="evt", raw_json_body=body)
    assert not signing.verify(
        signature_header=signature,
        timestamp=500,
        event_id="evt",
        raw_json_body=body,
        now=500 + signing.DEFAULT_TOLERANCE_SECONDS + 1,
        secrets=["k"],
    )
    assert not signing.verify(
        signature_header=signature,
        timestamp=500,
        event_id="evt",
        raw_json_body=body + " ",
        now=500,
        secrets=["k"],
    )
    assert not signing.verify(
        signature_header=signature,
        timestamp=500,
        event_id="evt",
        raw_json_body=body,
        now=500,
        secrets=["", "other"],
    )
    assert not signing.verify(
        signature_header="v1=short",
        timestamp=500,
        event_id="evt",
        raw_json_body=body,
        now=500,
        secrets=["k"],
    )


def test_build_headers_carries_the_signature_and_optional_key_id() -> None:
    headers = signing.build_headers(
        event_id="evt",
        event_type="session.failed",
        attempt=2,
        timestamp=9,
        key_id=None,
        signature="v1=abc",
    )
    assert headers["X-Omnigent-Signature"] == "v1=abc"
    assert headers["X-Omnigent-Event-Type"] == "session.failed"
    assert headers["X-Omnigent-Delivery-Attempt"] == "2"
    assert "X-Omnigent-Key-Id" not in headers
    with_key = signing.build_headers(
        event_id="evt",
        event_type="session.failed",
        attempt=1,
        timestamp=9,
        key_id="grok-bot",
        signature="v1=abc",
    )
    assert with_key["X-Omnigent-Key-Id"] == "grok-bot"


def test_signing_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(signing.SECRET_ENV_VAR, raising=False)
    monkeypatch.delenv(signing.PREVIOUS_SECRET_ENV_VAR, raising=False)
    assert signing.current_signing_key() is None
    assert signing.previous_secret() is None
    monkeypatch.setenv(signing.SECRET_ENV_VAR, "live")
    monkeypatch.setenv(signing.PREVIOUS_SECRET_ENV_VAR, "old")
    assert signing.current_signing_key() == "live"
    assert signing.previous_secret() == "old"
