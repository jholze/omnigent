"""Validation for the ``manager_webhook`` config block."""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.server.server_config import (
    MANAGER_WEBHOOK_EVENT_TYPES,
    ManagerWebhookConfigError,
    manager_webhook_config,
    reset_manager_webhook_config_cache,
)


def _write(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("OMNIGENT_CONFIG", str(path))
    reset_manager_webhook_config_cache()


def test_missing_block_is_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OMNIGENT_CONFIG", raising=False)
    reset_manager_webhook_config_cache()
    config = manager_webhook_config(refresh=True)
    assert config.enabled is False
    assert config.endpoint is None
    assert config.events == MANAGER_WEBHOOK_EVENT_TYPES
    assert config.wants("session.awaiting_decision")
    assert config.wants("session.decision_resolved")


def test_enabled_https_block_parses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  key_id: grok-bot
  public_base_url: https://omnigent.example.com/
  events:
    - session.awaiting_decision
    - session.awaiting_decision
    - session.completed
  timeout_seconds: 3.5
  max_attempts: 4
  summary_max_chars: 80
  extra_headers:
    X-Receiver-Route: grok-bot
""",
        monkeypatch,
    )
    config = manager_webhook_config(refresh=True)
    assert config.enabled is True
    assert config.endpoint == "https://bot.example.com/hook"
    assert config.public_base_url == "https://omnigent.example.com"
    assert config.events == ("session.awaiting_decision", "session.completed")
    assert config.wants("session.completed")
    assert not config.wants("session.failed")
    assert config.timeout_seconds == 3.5
    assert config.max_attempts == 4
    assert config.summary_max_chars == 80
    assert config.extra_headers["X-Receiver-Route"] == "grok-bot"
    assert config.key_id == "grok-bot"


def test_http_is_localhost_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: http://127.0.0.1:9/hook
  allow_insecure_localhost: true
""",
        monkeypatch,
    )
    assert manager_webhook_config(refresh=True).endpoint == "http://127.0.0.1:9/hook"

    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: http://127.0.0.1:9/hook
""",
        monkeypatch,
    )
    with pytest.raises(ManagerWebhookConfigError, match="HTTPS"):
        manager_webhook_config(refresh=True)

    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: http://bot.example.com/hook
  allow_insecure_localhost: true
""",
        monkeypatch,
    )
    with pytest.raises(ManagerWebhookConfigError, match="HTTPS"):
        manager_webhook_config(refresh=True)


def test_disabled_garbage_endpoint_does_not_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: false
  endpoint: not a url
""",
        monkeypatch,
    )
    config = manager_webhook_config(refresh=True)
    assert config.enabled is False
    assert config.endpoint == "not a url"


def test_rejects_bad_events_headers_bounds_and_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cases = [
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://user:pass@bot.example.com/hook
""",
            "credentials",
        ),
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  events: [session.nope]
""",
            "unknown",
        ),
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  extra_headers:
    Content-Type: text/plain
""",
            "cannot override",
        ),
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  extra_headers:
    X-Note: "line\\nbreak"
""",
            "single line",
        ),
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  timeout_seconds: 0.01
""",
            "timeout_seconds",
        ),
        (
            """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  max_attempts: 0
""",
            "max_attempts",
        ),
        (
            """
manager_webhook:
  enabled: true
""",
            "endpoint is not set",
        ),
        (
            """
manager_webhook:
  enabled: false
  public_base_url: http://bot.example.com
""",
            "public_base_url",
        ),
    ]
    for body, match in cases:
        _write(tmp_path, body, monkeypatch)
        with pytest.raises(ManagerWebhookConfigError, match=match):
            manager_webhook_config(refresh=True)
