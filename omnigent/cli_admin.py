"""``omnigent admin`` — server-local operator commands.

``omnigent admin agent-account create|list|revoke`` manages agent accounts
(``ogc_`` bots) directly through the chat store, without HTTP and without a
human session. It is meant to run on the server machine as the OS user that
owns the database, e.g. from a provisioning script. Whoever can run it can
already read the database and the HMAC secret, so it widens no trust
boundary; it replaces an interactive ``/auth/login`` for routine bot setup.

The same validation as ``POST /v1/agent-accounts`` applies (handle, display
name, project scope owned by the owner, webhook SSRF check, per-owner limit
from ``OMNIGENT_AGENT_ACCOUNT_LIMIT``). Every change appends one JSON line to
``<data_dir>/admin-audit.jsonl`` (0600) with the action, account id, owner,
OS user and outcome — never the token, webhook secret, or webhook URL.

The raw token and webhook secret are written only to a new 0600 credentials
file (``O_EXCL``), never to stdout.
"""

from __future__ import annotations

import getpass
import json
import os
import plistlib
import stat
import time
from pathlib import Path
from typing import Any

import click

_SECRET_ENV = "OMNIGENT_CHAT_AGENT_SECRET"
_LIMIT_ENV = "OMNIGENT_AGENT_ACCOUNT_LIMIT"
_PLIST_ENV_KEYS = (_SECRET_ENV, _LIMIT_ENV)
_AUDIT_FILE = "admin-audit.jsonl"


def _data_dir() -> Path:
    from omnigent.host.local_server import _local_data_dir

    return Path(_local_data_dir())


def _load_launchd_plist(path: str) -> str | None:
    """Adopt the server's agent secret, limit and DB URI from its launchd plist.

    Only keys in :data:`_PLIST_ENV_KEYS` are copied into this process's
    environment, and only when unset here. Values are never echoed.

    :returns: The plist's ``--database-uri`` argument, if any.
    """
    plist_path = Path(path).expanduser()
    try:
        info = plist_path.stat()
    except OSError as exc:
        raise click.ClickException(f"cannot read launchd plist {plist_path}: {exc}") from exc
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise click.ClickException(
            f"{plist_path} is readable by group/others; chmod 600 it before using it here."
        )
    with plist_path.open("rb") as fh:
        data = plistlib.load(fh)
    env = data.get("EnvironmentVariables") or {}
    for key in _PLIST_ENV_KEYS:
        value = env.get(key)
        if isinstance(value, str) and value.strip() and not os.environ.get(key, "").strip():
            os.environ[key] = value
    args = data.get("ProgramArguments") or []
    for index, arg in enumerate(args):
        if arg == "--database-uri" and index + 1 < len(args):
            return str(args[index + 1])
        if isinstance(arg, str) and arg.startswith("--database-uri="):
            return arg.split("=", 1)[1]
    return None


def _resolve_db_uri(database_uri: str | None, launchd_plist: str | None) -> str:
    plist_uri = _load_launchd_plist(launchd_plist) if launchd_plist else None
    uri = database_uri or plist_uri or f"sqlite:///{_data_dir() / 'chat.db'}"
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(uri)
    except ArgumentError as exc:
        raise click.ClickException(f"invalid database URI: {exc}") from exc
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        if not Path(url.database).exists():
            raise click.ClickException(
                f"database file {url.database} does not exist; refusing to create one."
            )
    return uri


def _open_chat_store(uri: str) -> Any:
    from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore

    return SqlAlchemyChatStore(uri)


def _audit(action: str, *, account_id: str | None, owner_id: str, outcome: str) -> None:
    """Append one audit line. Never receives the token, secret, or webhook URL."""
    from omnigent.debug_logging import add_audit_attrs

    add_audit_attrs(action=action, account_id=account_id, owner_id=owner_id, outcome=outcome)
    record = {
        "ts": int(time.time()),
        "action": action,
        "account_id": account_id,
        "owner_id": owner_id,
        "outcome": outcome,
        "os_user": getpass.getuser(),
        "via": "omnigent admin",
    }
    path = _data_dir() / _AUDIT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _require_owner(uri: str, owner_id: str) -> None:
    """The owner must be a live human user (or the single-user ``local``)."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from omnigent.db.db_models import SqlUser
    from omnigent.db.utils import get_or_create_engine
    from omnigent.server.auth import RESERVED_USER_LOCAL
    from omnigent.server.chat_access import principal_kind

    if principal_kind(owner_id) == "agent_account":
        raise click.ClickException("agent accounts cannot own agent accounts")
    if owner_id == RESERVED_USER_LOCAL:
        return
    with Session(get_or_create_engine(uri)) as session:
        user = session.execute(select(SqlUser).where(SqlUser.id == owner_id)).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise click.ClickException(f"owner {owner_id!r} is not a live user")


def _write_credentials(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _omnigent_error(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


def _find_account(store: Any, owner_id: str, ref: str) -> Any | None:
    for account in store.list_active_agent_accounts():
        if account.owner_id != owner_id:
            continue
        if ref in (account.id, account.handle, account.principal_id):
            return account
    return None


_DB_OPTION = click.option(
    "--database-uri", default=None, help="Server database URI (default: <data_dir>/chat.db)."
)
_PLIST_OPTION = click.option(
    "--launchd-plist",
    default=None,
    help=(
        "Read OMNIGENT_CHAT_AGENT_SECRET, OMNIGENT_AGENT_ACCOUNT_LIMIT and --database-uri "
        "from this launchd plist (must be 0600). Env vars and --database-uri win."
    ),
)


def register_admin_commands(cli: click.Group) -> None:
    """Attach the ``admin`` group to the root CLI."""

    @cli.group("admin")
    def admin() -> None:
        """Server-local operator commands (run on the server machine)."""

    @admin.group("agent-account")
    def agent_account() -> None:
        """Create, list, or revoke agent accounts without a login."""

    @agent_account.command("create")
    @click.option("--owner", "owner_id", required=True, help="Owning human user id, e.g. jholze.")
    @click.option("--handle", required=True, help="Lowercase handle, 2-40 chars.")
    @click.option("--display-name", required=True, help="Display name, 1-40 chars.")
    @click.option(
        "--project",
        "project_ids",
        multiple=True,
        help="Allowed project id (repeatable). Omit only with --unscoped.",
    )
    @click.option(
        "--unscoped", is_flag=True, help="Create with full owner rights (no project scope)."
    )
    @click.option("--webhook-url", default=None, help="Optional webhook URL (SSRF-checked).")
    @click.option(
        "--out",
        "out_path",
        default=None,
        help="Credentials file (default ~/.local/share/omnigent-bots/<handle>.credentials).",
    )
    @click.option("--server-url", default=None, help="Public server URL stored in the file.")
    @click.option("--local-server-url", default=None, help="Local server URL stored in the file.")
    @_DB_OPTION
    @_PLIST_OPTION
    def create_command(
        owner_id: str,
        handle: str,
        display_name: str,
        project_ids: tuple[str, ...],
        unscoped: bool,
        webhook_url: str | None,
        out_path: str | None,
        server_url: str | None,
        local_server_url: str | None,
        database_uri: str | None,
        launchd_plist: str | None,
    ) -> None:
        """Mint an agent account. The token goes only into a new 0600 file."""
        from omnigent.errors import OmnigentError
        from omnigent.server.agent_account_tokens import (
            agent_account_limit,
            agent_account_secret,
            derive_webhook_secret,
            webhook_url_rejected,
        )
        from omnigent.server.routes.agent_accounts import (
            _validate_display_name,
            _validate_handle,
            _validate_project_ids,
        )
        from omnigent.stores.chat_store import AccountStatus

        if bool(project_ids) == unscoped:
            raise click.ClickException("pass at least one --project, or --unscoped (not both)")
        uri = _resolve_db_uri(database_uri, launchd_plist)
        if agent_account_secret() is None:
            raise click.ClickException(
                f"{_SECRET_ENV} is not set. Export it or pass --launchd-plist; it must match "
                "the server's value or the token will not authenticate."
            )
        try:
            name = _validate_display_name(display_name)
            handle = _validate_handle(handle)
            scope = _validate_project_ids(list(project_ids) if project_ids else None)
        except OmnigentError as exc:
            raise _omnigent_error(exc) from exc
        webhook = (webhook_url or "").strip()
        if webhook and webhook_url_rejected(webhook):
            raise click.ClickException("webhook_url is invalid")
        target = Path(
            out_path or f"~/.local/share/omnigent-bots/{handle}.credentials"
        ).expanduser()
        if target.exists():
            raise click.ClickException(f"{target} already exists; refusing to overwrite.")
        _require_owner(uri, owner_id)
        store = _open_chat_store(uri)
        result = store.create_agent_account(
            owner_id, name, handle, webhook_url=webhook, project_ids=scope
        )
        if result.status is AccountStatus.HANDLE_TAKEN:
            _audit(
                "chat.agent_account.create",
                account_id=None,
                owner_id=owner_id,
                outcome="handle_taken",
            )
            raise click.ClickException("handle already exists")
        if result.status is AccountStatus.PROJECT_NOT_FOUND:
            _audit(
                "chat.agent_account.create",
                account_id=None,
                owner_id=owner_id,
                outcome="project_not_found",
            )
            raise click.ClickException("project not found or not owned by the owner")
        if result.status is AccountStatus.LIMIT:
            _audit(
                "chat.agent_account.create",
                account_id=None,
                owner_id=owner_id,
                outcome="agent_account_limit",
            )
            raise click.ClickException(
                f"agent account limit reached ({agent_account_limit()} active per owner; "
                f"raise {_LIMIT_ENV} on the server and here)"
            )
        if result.issue is None:
            raise click.ClickException("agent account was not created")
        account = result.issue.account
        payload = {
            "server": server_url,
            "local_server": local_server_url,
            "account_id": account.id,
            "handle": account.handle,
            "owner_id": account.owner_id,
            "project_ids": list(account.project_ids) if account.project_ids is not None else None,
            "token": result.issue.token,
            "webhook_secret": derive_webhook_secret(account.id, account.secret_version),
        }
        try:
            _write_credentials(target, payload)
        except OSError as exc:
            # The account exists but nobody has its token: revoke it so no
            # orphaned credential stays live.
            store.revoke_agent_account(account.id, owner_id)
            _audit(
                "chat.agent_account.create",
                account_id=account.id,
                owner_id=owner_id,
                outcome="revoked_write_failed",
            )
            raise click.ClickException(
                f"could not write {target}: {exc}; account revoked"
            ) from exc
        _audit(
            "chat.agent_account.create",
            account_id=account.id,
            owner_id=owner_id,
            outcome="created",
        )
        click.echo(
            f"created {account.handle} (id {account.id[:8]}…, prefix {account.token_prefix}), "
            f"scope {payload['project_ids']}"
        )
        click.echo(f"credentials: {target} (0600)")

    @agent_account.command("list")
    @click.option("--owner", "owner_id", default=None, help="Only accounts of this owner.")
    @click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
    @_DB_OPTION
    @_PLIST_OPTION
    def list_command(
        owner_id: str | None, as_json: bool, database_uri: str | None, launchd_plist: str | None
    ) -> None:
        """List active agent accounts (no secrets)."""
        from omnigent.server.agent_account_tokens import agent_account_limit

        uri = _resolve_db_uri(database_uri, launchd_plist)
        accounts = [
            a
            for a in _open_chat_store(uri).list_active_agent_accounts()
            if owner_id is None or a.owner_id == owner_id
        ]
        rows = [
            {
                "id": a.id,
                "handle": a.handle,
                "display_name": a.display_name,
                "owner_id": a.owner_id,
                "token_prefix": a.token_prefix,
                "project_ids": list(a.project_ids) if a.project_ids is not None else None,
            }
            for a in accounts
        ]
        if as_json:
            click.echo(json.dumps({"limit": agent_account_limit(), "accounts": rows}))
            return
        for row in rows:
            scope = ",".join(row["project_ids"]) if row["project_ids"] else "unscoped"
            click.echo(f"{row['handle']}\t{row['id'][:8]}…\t{row['owner_id']}\t{scope}")
        owners = {r["owner_id"] for r in rows}
        for owner in sorted(owners):
            count = sum(1 for r in rows if r["owner_id"] == owner)
            click.echo(f"# {owner}: {count}/{agent_account_limit()} active")

    @agent_account.command("revoke")
    @click.option("--owner", "owner_id", required=True, help="Owning user id.")
    @click.argument("account")
    @click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
    @_DB_OPTION
    @_PLIST_OPTION
    def revoke_command(
        owner_id: str, account: str, yes: bool, database_uri: str | None, launchd_plist: str | None
    ) -> None:
        """Revoke an account by handle or id. Its token stops working at once."""
        from omnigent.stores.chat_store import AccountStatus

        uri = _resolve_db_uri(database_uri, launchd_plist)
        store = _open_chat_store(uri)
        found = _find_account(store, owner_id, account)
        if found is None:
            raise click.ClickException("agent account not found")
        if not yes:
            click.confirm(f"Revoke {found.handle} ({found.id[:8]}…)?", abort=True)
        result = store.revoke_agent_account(found.id, owner_id)
        if result.status is AccountStatus.NOT_FOUND:
            raise click.ClickException("agent account not found")
        _audit(
            "chat.agent_account.revoke", account_id=found.id, owner_id=owner_id, outcome="revoked"
        )
        click.echo(f"revoked {found.handle}")
