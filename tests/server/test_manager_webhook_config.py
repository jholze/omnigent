"""Validation for the ``manager_webhook`` config block."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from omnigent.server.server_config import (
    MANAGER_WEBHOOK_EVENT_TYPES,
    ManagerWebhookConfigError,
    manager_webhook_config,
    reset_manager_webhook_config_cache,
    webhook_public_base_url_or_none,
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


def test_disabled_endpoint_is_still_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: false
  endpoint: https://bot.example.com/hook
""",
        monkeypatch,
    )
    config = manager_webhook_config(refresh=True)
    assert config.enabled is False
    assert config.endpoint == "https://bot.example.com/hook"

    for endpoint in (
        "not a url",
        "HTTP://example.com/hook",
        "http://example.com/hook",
    ):
        _write(
            tmp_path,
            f"""
manager_webhook:
  enabled: false
  endpoint: "{endpoint}"
  allow_insecure_localhost: true
""",
            monkeypatch,
        )
        with pytest.raises(ManagerWebhookConfigError, match="HTTPS"):
            manager_webhook_config(refresh=True)


def test_https_rejects_private_link_local_and_metadata_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocked = (
        "https://10.1.2.3/hook",
        "https://192.168.1.9/hook",
        "https://169.254.169.254/latest",
        "https://2852039166/",
        "https://0xa9fea9fe/",
        "https://0251.0376.0251.0376/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://metadata.google.internal./",
        "https://metadata.google.com/",
        "https://[fc00::1]/hook",
        "https://[fe80::1]/hook",
        "https://[::ffff:10.0.0.1]/hook",
    )
    for endpoint in blocked:
        _write(
            tmp_path,
            f"""
manager_webhook:
  enabled: true
  endpoint: "{endpoint}"
""",
            monkeypatch,
        )
        with pytest.raises(ManagerWebhookConfigError, match="private, link-local, or metadata"):
            manager_webhook_config(refresh=True)

    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: https://127.0.0.1/hook
""",
        monkeypatch,
    )
    assert manager_webhook_config(refresh=True).endpoint == "https://127.0.0.1/hook"
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: https://0x7f000001/hook
""",
        monkeypatch,
    )
    assert manager_webhook_config(refresh=True).endpoint == "https://0x7f000001/hook"


def test_public_base_url_may_be_a_lan_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  public_base_url: https://192.168.1.10:8443
""",
        monkeypatch,
    )
    config = manager_webhook_config(refresh=True)
    assert config.endpoint == "https://bot.example.com/hook"
    assert config.public_base_url == "https://192.168.1.10:8443"

    _write(
        tmp_path,
        """
manager_webhook:
  enabled: true
  endpoint: https://bot.example.com/hook
  public_base_url: http://192.168.1.10:8443
""",
        monkeypatch,
    )
    with pytest.raises(ManagerWebhookConfigError, match="public_base_url"):
        manager_webhook_config(refresh=True)


def test_invalid_webhook_config_disables_the_feature_and_still_boots(
    db_uri: str, caplog: pytest.LogCaptureFixture
) -> None:
    from omnigent.server.app import _wire_manager_webhook
    from omnigent.server.server_config import reset_manager_webhook_config_cache

    class _ConversationStore:
        storage_location = db_uri

    reset_manager_webhook_config_cache()
    caplog.set_level(logging.ERROR, logger="omnigent.server.app")
    store = _wire_manager_webhook(
        conversation_store=_ConversationStore(),  # type: ignore[arg-type]
        agent_store=None,  # type: ignore[arg-type]
        project_store=None,
        server_config={
            "manager_webhook": {
                "enabled": True,
                "endpoint": "https://192.168.1.10/hook",
                "public_base_url": "https://192.168.1.10:8443",
            }
        },
    )
    assert store is not None
    disabled = manager_webhook_config()
    assert disabled.enabled is False
    assert any(
        record.levelno >= logging.ERROR and "disabling the feature" in record.message
        for record in caplog.records
    )


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


@pytest.mark.parametrize(
    ("url", "allow_insecure_localhost", "expected"),
    [
        pytest.param(None, False, None, id="unset"),
        pytest.param(
            "https://omnigent.example.com/", False, "https://omnigent.example.com", id="https"
        ),
        pytest.param("http://lan.example.com", False, None, id="http-rejected"),
        pytest.param("http://lan.example.com", True, None, id="http-non-loopback-rejected"),
        pytest.param("http://localhost:6767", False, None, id="http-loopback-not-allowed"),
        pytest.param(
            "http://localhost:6767/", True, "http://localhost:6767", id="http-loopback-allowed"
        ),
    ],
)
def test_webhook_public_base_url_or_none_applies_public_base_url_rule(
    url: str | None, allow_insecure_localhost: bool, expected: str | None
) -> None:
    """A fallback origin follows the ``manager_webhook.public_base_url`` rule."""
    assert (
        webhook_public_base_url_or_none(url, allow_insecure_localhost=allow_insecure_localhost)
        == expected
    )
