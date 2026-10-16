"""``omnigent manager-webhook`` test, list, and retry commands."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from omnigent.server.server_config import reset_manager_webhook_config_cache
from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
    SqlAlchemySessionLifecycleStore,
)


class _Receiver(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.bodies: list[bytes] = []
        self.headers: list[dict[str, str]] = []

    @property
    def url(self) -> str:
        _host, port = self.server_address[:2]
        return f"http://127.0.0.1:{port}/hook"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server: _Receiver = self.server  # type: ignore[assignment]
        server.bodies.append(body)
        server.headers.append(dict(self.headers.items()))
        payload = b"accepted"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def receiver() -> Iterator[_Receiver]:
    server = _Receiver()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("OMNIGENT_CONFIG", str(path))
    reset_manager_webhook_config_cache()


def test_test_command_posts_a_signed_probe(
    receiver: _Receiver, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnigent.cli import cli
    from omnigent.server import manager_webhook_signing as signing

    _config(
        tmp_path,
        monkeypatch,
        "manager_webhook:\n"
        "  enabled: true\n"
        f"  endpoint: {receiver.url}\n"
        "  allow_insecure_localhost: true\n"
        "  key_id: grok-bot\n",
    )
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", "cli-test-secret")
    result = CliRunner().invoke(cli, ["manager-webhook", "test"])
    assert result.exit_code == 0, result.output
    assert receiver.bodies
    raw = receiver.bodies[0].decode("utf-8")
    payload = json.loads(raw)
    assert payload["event_type"] == "manager_webhook.test"
    assert payload["data"]["message"] == "omnigent manager-webhook test"
    headers = receiver.headers[0]
    timestamp = int(headers["X-Omnigent-Timestamp"])
    assert signing.verify(
        signature_header=headers["X-Omnigent-Signature"],
        timestamp=timestamp,
        event_id=headers["X-Omnigent-Event-Id"],
        raw_json_body=raw,
        now=timestamp,
        secrets=["cli-test-secret"],
    )
    assert headers["X-Omnigent-Key-Id"] == "grok-bot"


def test_test_command_refuses_non_local_http_even_with_the_localhost_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnigent.cli import cli

    posted: list[str] = []

    def _post(*_args: object, **_kwargs: object) -> None:
        posted.append("sent")
        raise AssertionError("test command posted to a foreign http endpoint")

    monkeypatch.setattr("omnigent.cli_manager_webhook.httpx.post", _post)
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", "cli-test-secret")
    for endpoint in ("HTTP://example.com/hook", "http://example.com/hook"):
        _config(
            tmp_path,
            monkeypatch,
            "manager_webhook:\n"
            "  enabled: false\n"
            f"  endpoint: {endpoint}\n"
            "  allow_insecure_localhost: true\n",
        )
        refused = CliRunner().invoke(cli, ["manager-webhook", "test"])
        assert refused.exit_code != 0, refused.output
        assert "HTTPS" in refused.output
    assert posted == []


def test_test_command_refuses_plaintext_and_missing_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnigent.cli import cli

    _config(
        tmp_path,
        monkeypatch,
        "manager_webhook:\n  enabled: true\n  endpoint: http://127.0.0.1:9/hook\n",
    )
    monkeypatch.setenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", "cli-test-secret")
    refused = CliRunner().invoke(cli, ["manager-webhook", "test"])
    assert refused.exit_code != 0
    assert "http://" in refused.output

    _config(
        tmp_path,
        monkeypatch,
        "manager_webhook:\n  enabled: true\n  endpoint: https://bot.example.com/hook\n",
    )
    monkeypatch.delenv("OMNIGENT_MANAGER_WEBHOOK_SECRET", raising=False)
    missing = CliRunner().invoke(cli, ["manager-webhook", "test"])
    assert missing.exit_code != 0
    assert "OMNIGENT_MANAGER_WEBHOOK_SECRET" in missing.output


def test_dead_letters_and_retry(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnigent.cli import cli

    _config(
        tmp_path,
        monkeypatch,
        "manager_webhook:\n  enabled: false\n",
    )
    store = SqlAlchemySessionLifecycleStore(db_uri)
    session_id = uuid.uuid4().hex
    event_id = uuid.uuid4().hex
    store.record_lifecycle_event(
        event_id=event_id,
        session_id=session_id,
        event_type="session.failed",
        transition_key="turn:1:failed",
        payload='{"response_id":"r"}',
        now=int(time.time()),
    )
    claimed = store.claim_batch(
        limit=1, now=int(time.time()) + 5, lease_owner="t", lease_seconds=10
    )
    store.mark_delivery_failed(
        claimed[0].id,
        workspace_id=claimed[0].workspace_id,
        next_attempt_at=int(time.time()) + 60,
        dead_letter_after_attempts=1,
        http_status=500,
        error_code="http_error",
        error_message="manager returned 500",
    )
    listed = CliRunner().invoke(cli, ["manager-webhook", "dead-letters", "--database-uri", db_uri])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert rows[0]["event_id"] == event_id
    assert rows[0]["last_http_status"] == 500
    retried = CliRunner().invoke(
        cli,
        ["manager-webhook", "retry", "--database-uri", db_uri, "--event-id", event_id],
    )
    assert retried.exit_code == 0, retried.output
    assert "requeued 1" in retried.output
    assert store.latest_delivery(session_id).status == "pending"  # type: ignore[union-attr]
    assert store.latest_delivery(session_id).attempt_count == 0  # type: ignore[union-attr]
