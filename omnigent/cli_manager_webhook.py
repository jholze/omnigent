"""``omnigent manager-webhook`` — probe the receiver and manage dead letters."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import click
import httpx

from omnigent.entities import LifecycleOutboxEvent
from omnigent.server import manager_webhook_signing as signing
from omnigent.server.manager_webhook_dispatcher import render_delivery_body
from omnigent.server.server_config import (
    ManagerWebhookConfigError,
    _validate_webhook_url,
    manager_webhook_config,
    reset_manager_webhook_config_cache,
)


def register_manager_webhook_commands(cli: click.Group) -> None:
    """Attach the ``manager-webhook`` group to the root CLI."""

    @cli.group("manager-webhook")
    def manager_webhook() -> None:
        """Send a signed test event or manage dead-lettered deliveries."""

    @manager_webhook.command("test")
    @click.option(
        "--database-uri",
        default=None,
        help="Unused by test. Accepted so the flag matches the other subcommands.",
    )
    def test_command(database_uri: str | None) -> None:
        """POST one signed ``manager_webhook.test`` event to the configured endpoint."""
        del database_uri
        reset_manager_webhook_config_cache()
        try:
            config = manager_webhook_config(refresh=True)
        except ManagerWebhookConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        if not config.endpoint:
            raise click.ClickException(
                "manager_webhook.endpoint is not set. Add it to config.yaml first."
            )
        secret = signing.current_signing_key()
        if secret is None:
            raise click.ClickException(
                f"{signing.SECRET_ENV_VAR} is not set. Export it and retry."
            )
        try:
            _validate_webhook_url(
                config.endpoint,
                key="endpoint",
                allow_insecure_localhost=config.allow_insecure_localhost,
                block_private_hosts=True,
            )
        except ManagerWebhookConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        event = LifecycleOutboxEvent(
            id=uuid.uuid4().hex,
            session_id="0" * 32,
            event_type="manager_webhook.test",
            transition_key="cli:test",
            sequence=0,
            event_version=1,
            payload=json.dumps({"message": "omnigent manager-webhook test"}),
            status="pending",
            attempt_count=1,
            next_attempt_at=int(time.time()),
            created_at=int(time.time()),
            workspace_id=0,
        )
        body = render_delivery_body(event)
        timestamp = int(time.time())
        signature = signing.sign(
            secret=secret, timestamp=timestamp, event_id=event.id, raw_json_body=body
        )
        headers = signing.build_headers(
            event_id=event.id,
            event_type=event.event_type,
            attempt=1,
            timestamp=timestamp,
            key_id=config.key_id,
            signature=signature,
        )
        for name, value in config.extra_headers.items():
            headers.setdefault(name, value)
        try:
            response = httpx.post(
                config.endpoint,
                content=body,
                headers=headers,
                timeout=config.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise click.ClickException(f"delivery failed: {type(exc).__name__}: {exc}") from exc
        click.echo(f"POST {config.endpoint} -> {response.status_code}")
        snippet = response.text[:300].replace("\n", " ")
        if snippet:
            click.echo(snippet)
        if response.status_code < 200 or response.status_code >= 300:
            raise SystemExit(1)

    @manager_webhook.command("dead-letters")
    @click.option("--database-uri", default=None, help="Override the server database URI.")
    @click.option("--limit", default=50, show_default=True, type=int)
    def dead_letters_command(database_uri: str | None, limit: int) -> None:
        """List dead-lettered deliveries as JSON."""
        store = _open_store(database_uri)
        rows = store.list_by_status("dead_letter", limit=limit)
        payload = [
            {
                "event_id": row.id,
                "event_type": row.event_type,
                "session_id": row.session_id,
                "sequence": row.sequence,
                "attempt_count": row.attempt_count,
                "last_http_status": row.last_http_status,
                "last_error_code": row.last_error_code,
                "last_error_message": row.last_error_message,
            }
            for row in rows
        ]
        click.echo(json.dumps(payload, indent=2))

    @manager_webhook.command("retry")
    @click.option("--database-uri", default=None, help="Override the server database URI.")
    @click.option(
        "--event-id",
        "event_ids",
        multiple=True,
        help="Retry one event. Repeat to retry several. Omit to retry every dead letter.",
    )
    def retry_command(database_uri: str | None, event_ids: tuple[str, ...]) -> None:
        """Move dead-lettered rows back to pending so the running server delivers them."""
        store = _open_store(database_uri)
        count = store.requeue_dead_letters(
            list(event_ids) if event_ids else None, now=int(time.time())
        )
        click.echo(f"requeued {count} dead-lettered deliver{'y' if count == 1 else 'ies'}")
        if count:
            click.echo(
                "The server dispatcher picks these up on its next poll "
                "(within a couple of seconds if it is already running)."
            )


def _open_store(database_uri: str | None) -> Any:
    from omnigent.db.utils import get_or_create_engine
    from omnigent.host.local_server import _local_data_dir
    from omnigent.stores.session_lifecycle_store.sqlalchemy_store import (
        SqlAlchemySessionLifecycleStore,
    )

    uri = database_uri or f"sqlite:///{_local_data_dir() / 'chat.db'}"
    # Opening the engine migrates an existing database to head, including
    # the additive outbox tables.
    get_or_create_engine(uri)
    return SqlAlchemySessionLifecycleStore(uri)
