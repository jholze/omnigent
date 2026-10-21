#!/usr/bin/env python3
"""Seed a disposable team-chat instance with demo users, channels, and one approval.

The script is repeatable: stable ``client_id`` values make message posts
idempotent, and an existing agent session is reused. Passwords are read from
a mode-600 file and are never printed.

The open approval card stays up only while ``--hold`` is running. The
permission hook times out after 24 hours, or sooner when someone answers.
Point ``--hold`` at the server itself (``http://127.0.0.1:<port>``). A public
proxy such as a Cloudflare quick tunnel closes that long poll after about
100 seconds and the card disappears.

Example::

    python scripts/seed_team_chat_demo.py \\
        --base-url http://127.0.0.1:6791 \\
        --credentials /path/credentials \\
        --database-uri sqlite:////path/demo.db \\
        --hold
"""

from __future__ import annotations

import argparse
import io
import json
import re
import secrets
import stat
import sys
import tarfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_USERS = ("jens", "anna", "bob")
_ADMIN = "jens"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_MENTION = "<@omnigent_agent:"
_LEVEL_READ = 1
_LEVEL_EDIT = 2
_AGENT_RESPONSE_ID = "resp_demo_migration"
_AGENT_REPLY = "Ich warte auf die Freigabe, bevor ich die Migration anwende."
_HOLD_TIMEOUT_S = 90_000.0
_ELICITATION_ID_RE = re.compile(r"^elicit_[a-z]+_[0-9a-f]{32}$")


@dataclass(frozen=True)
class SeedMessage:
    """One chat post with a stable idempotency key."""

    client_id: str
    author: str
    body: str


@dataclass(frozen=True)
class SeedThread:
    """A human thread: one root and its replies."""

    root: SeedMessage
    replies: tuple[SeedMessage, ...]


@dataclass(frozen=True)
class SeedChannel:
    """A public channel, its top-level lines, and one human thread."""

    name: str
    topic: str
    messages: tuple[SeedMessage, ...]
    thread: SeedThread


@dataclass(frozen=True)
class SeedDirect:
    """A two-person DM. ``other`` is the principal the opener names."""

    opener: str
    other: str
    messages: tuple[SeedMessage, ...]


@dataclass(frozen=True)
class SeedAgent:
    """The channel root that is linked to a session and an approval."""

    channel: str
    root: SeedMessage
    reply_text: str
    response_id: str


@dataclass(frozen=True)
class SeedPlan:
    """Everything the demo instance should contain."""

    users: tuple[str, ...]
    admin: str
    pointer: SeedMessage
    channels: tuple[SeedChannel, ...]
    direct: SeedDirect
    agent: SeedAgent


def demo_plan() -> SeedPlan:
    """Return the fixed demo content. Bodies stay free of agent-start mentions."""
    return SeedPlan(
        users=_USERS,
        admin=_ADMIN,
        pointer=SeedMessage(
            "demo-general-pointer",
            "jens",
            "Demo: schau in #produkt, #design und #betrieb. Die Freigabe liegt in #produkt.",
        ),
        channels=(
            SeedChannel(
                name="produkt",
                topic="Produkt und heutige Demo",
                messages=(
                    SeedMessage(
                        "demo-produkt-welcome",
                        "jens",
                        "Willkommen im Produkt-Kanal. Der Agent-Thread liegt hier unten.",
                    ),
                    SeedMessage(
                        "demo-produkt-note",
                        "anna",
                        "Die Freigabe-Karte bleibt offen, solange der Demo-Prozess läuft.",
                    ),
                ),
                thread=SeedThread(
                    root=SeedMessage(
                        "demo-produkt-thread",
                        "bob",
                        "Kurzer Thread: sollen wir heute Desktop und iPhone prüfen?",
                    ),
                    replies=(
                        SeedMessage(
                            "demo-produkt-thread-jens",
                            "jens",
                            "Ja. Bitte Status-Chip und Freigabe-Karte ansehen.",
                        ),
                        SeedMessage(
                            "demo-produkt-thread-anna",
                            "anna",
                            "Ich öffne den Agent-Thread danach noch einmal.",
                        ),
                    ),
                ),
            ),
            SeedChannel(
                name="design",
                topic="Entwürfe",
                messages=(
                    SeedMessage(
                        "demo-design-header",
                        "anna",
                        "Auf dem Telefon kann der Zurück-Link am Sidebar-Button hängen. Bekannt.",
                    ),
                    SeedMessage(
                        "demo-design-look",
                        "jens",
                        "Bitte trotzdem Karten, Abstände und lange Zeilen ansehen.",
                    ),
                ),
                thread=SeedThread(
                    root=SeedMessage(
                        "demo-design-thread",
                        "anna",
                        "Farbvorschlag für den Status: Grün fertig, Gelb wartet.",
                    ),
                    replies=(
                        SeedMessage(
                            "demo-design-thread-bob",
                            "bob",
                            "Einverstanden. Der Text soll lesbar bleiben.",
                        ),
                    ),
                ),
            ),
            SeedChannel(
                name="betrieb",
                topic="Nur diese Testinstanz",
                messages=(
                    SeedMessage(
                        "demo-betrieb-scope",
                        "bob",
                        "Diese Instanz ist nur für den manuellen Test. "
                        "Der normale Server bleibt unverändert.",
                    ),
                    SeedMessage(
                        "demo-betrieb-flag",
                        "jens",
                        "team_chat ist nur hier an. Bitte nichts an der Umgebung ändern.",
                    ),
                ),
                thread=SeedThread(
                    root=SeedMessage(
                        "demo-betrieb-thread",
                        "bob",
                        "Welche Grenzen soll ich beim Durchklicken notieren?",
                    ),
                    replies=(
                        SeedMessage(
                            "demo-betrieb-thread-jens",
                            "jens",
                            "Antwortzahl nach dem Neuladen, DM-Titel und der Autorname.",
                        ),
                    ),
                ),
            ),
        ),
        direct=SeedDirect(
            opener="jens",
            other="anna",
            messages=(
                SeedMessage(
                    "demo-dm-anna",
                    "anna",
                    "Direktnachricht: hier ist nur unser Gespräch, ohne Kanal.",
                ),
                SeedMessage(
                    "demo-dm-jens",
                    "jens",
                    "Der Titel kann Direktnachricht heißen, ohne unseren Namen.",
                ),
            ),
        ),
        agent=SeedAgent(
            channel="produkt",
            root=SeedMessage(
                "demo-agent-root",
                "anna",
                "Bitte die Migration prüfen, bevor wir sie anwenden.",
            ),
            reply_text=_AGENT_REPLY,
            response_id=_AGENT_RESPONSE_ID,
        ),
    )


def validate_plan(plan: SeedPlan) -> None:
    """Reject a plan that would start a real agent or post an unstable id."""
    if plan.admin not in plan.users:
        raise ValueError("admin is not one of the demo users")
    client_ids: list[str] = []
    names: list[str] = []
    for message in _all_messages(plan):
        if not _CLIENT_ID_RE.fullmatch(message.client_id):
            raise ValueError(f"client_id is not stable: {message.client_id}")
        if message.author not in plan.users:
            raise ValueError(f"unknown author on {message.client_id}")
        if _MENTION in message.body:
            raise ValueError(f"agent mention in {message.client_id}")
        client_ids.append(message.client_id)
    if len(client_ids) != len(set(client_ids)):
        raise ValueError("client_id values are not unique")
    for channel in plan.channels:
        if not _CHANNEL_RE.fullmatch(channel.name):
            raise ValueError(f"channel name is invalid: {channel.name}")
        names.append(channel.name)
    if len(names) != len(set(names)):
        raise ValueError("channel names are not unique")
    if plan.agent.channel not in names:
        raise ValueError("agent thread names a missing channel")
    if plan.direct.opener not in plan.users or plan.direct.other not in plan.users:
        raise ValueError("DM principals are not demo users")
    if plan.direct.opener == plan.direct.other:
        raise ValueError("DM needs two people")


def passwords_from_text(text: str) -> dict[str, str]:
    """Parse ``user=password`` lines. Unknown keys are ignored. Values stay secret."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name in _USERS:
            found[name] = value
    missing = [user for user in _USERS if not found.get(user)]
    if missing:
        raise ValueError("credentials file is missing a password for " + ", ".join(missing))
    return found


def assert_private_file(path: Path) -> None:
    """Refuse a credentials file other users can read."""
    mode = path.stat().st_mode
    if stat.S_ISDIR(mode) or mode & 0o077:
        raise ValueError(f"{path} must be a file with mode 600")


def _all_messages(plan: SeedPlan) -> tuple[SeedMessage, ...]:
    messages: list[SeedMessage] = [plan.pointer]
    for channel in plan.channels:
        messages.extend(channel.messages)
        messages.append(channel.thread.root)
        messages.extend(channel.thread.replies)
    messages.extend(plan.direct.messages)
    messages.append(plan.agent.root)
    return tuple(messages)


def new_elicitation_id() -> str:
    """Mint an id the permission hook accepts: ``elicit_<harness>_`` plus 32 hex chars."""
    elicitation_id = f"elicit_claude_{secrets.token_hex(16)}"
    if not _ELICITATION_ID_RE.fullmatch(elicitation_id):
        raise RuntimeError("elicitation id does not match the hook rule")
    return elicitation_id


def _agent_yaml() -> bytes:
    return b"""\
spec_version: 1
name: chat_agent
prompt: You answer in the team chat.

executor:
  model: gpt-4o-mini
  config:
    harness: openai-agents
"""


def _bundle_bytes() -> bytes:
    payload = _agent_yaml()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        info = tarfile.TarInfo("config.yaml")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class DemoApi:
    """Cookie-free bearer client. One token per demo user."""

    def __init__(self, base_url: str, passwords: dict[str, str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.passwords = passwords
        self.tokens: dict[str, str] = {}
        # Bearer only. A stored session cookie would override Authorization,
        # so a later login as anna would make the admin invite run as anna.
        self.http = httpx.Client(timeout=30.0, trust_env=False)

    def close(self) -> None:
        self.http.close()

    def _forget_cookies(self) -> None:
        self.http.cookies.clear()

    def login(self, username: str) -> None:
        response = self.http.post(
            f"{self.base_url}/auth/login",
            json={"username": username, "password": self.passwords[username]},
        )
        self._forget_cookies()
        if response.status_code != 200:
            _raise_for(response)
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"login for {username} returned no token")
        self.tokens[username] = token

    def register(self, username: str, invite: str) -> None:
        response = self.http.post(
            f"{self.base_url}/auth/register",
            json={
                "invite": invite,
                "username": username,
                "password": self.passwords[username],
            },
        )
        self._forget_cookies()
        if response.status_code != 200:
            _raise_for(response)
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"register for {username} returned no token")
        self.tokens[username] = token

    def invite(self) -> str:
        response = self._send(_ADMIN, "POST", "/auth/invite", json={"is_admin": False})
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("invite returned no token")
        return token

    def ensure_users(self) -> None:
        """Log in the admin, then invite anyone who cannot log in yet."""
        self.login(_ADMIN)
        for username in _USERS:
            if username == _ADMIN:
                continue
            probe = self.http.post(
                f"{self.base_url}/auth/login",
                json={"username": username, "password": self.passwords[username]},
            )
            self._forget_cookies()
            if probe.status_code == 200:
                token = probe.json().get("token")
                if not isinstance(token, str) or not token:
                    raise RuntimeError(f"login for {username} returned no token")
                self.tokens[username] = token
                continue
            if probe.status_code != 401:
                _raise_for(probe)
            self.register(username, self.invite())

    def bootstrap(self, username: str) -> dict[str, Any]:
        return self._send(username, "GET", "/v1/chat/bootstrap").json()

    def ensure_channel(self, channel: SeedChannel) -> str:
        created = self._send(
            _ADMIN,
            "POST",
            "/v1/chat/channels",
            json={"name": channel.name, "topic": channel.topic},
            allow=(201, 409),
        )
        if created.status_code == 201:
            channel_id = str(created.json()["id"])
        else:
            channel_id = self._channel_id(channel.name)
        for username in _USERS:
            self._send(
                username, "POST", f"/v1/chat/channels/{_quote(channel_id)}/join", allow=(204,)
            )
        return channel_id

    def post(
        self, channel_id: str, message: SeedMessage, thread_root_id: str | None = None
    ) -> str:
        body: dict[str, Any] = {"body": message.body, "client_id": message.client_id}
        if thread_root_id is not None:
            body["thread_root_id"] = thread_root_id
        response = self._send(
            message.author,
            "POST",
            f"/v1/chat/channels/{_quote(channel_id)}/messages",
            json=body,
            allow=(200, 201),
        )
        return str(response.json()["id"])

    def open_dm(self, direct: SeedDirect) -> str:
        response = self._send(
            direct.opener,
            "POST",
            "/v1/chat/dms",
            json={"principals": [direct.other]},
            allow=(200, 201),
        )
        return str(response.json()["id"])

    def thread_messages(
        self, username: str, channel_id: str, root_id: str
    ) -> list[dict[str, Any]]:
        response = self._send(
            username,
            "GET",
            f"/v1/chat/channels/{_quote(channel_id)}/messages",
            params={"tail": "true", "thread_root_id": root_id},
        )
        items = response.json().get("items")
        if not isinstance(items, list):
            raise RuntimeError("message page has no items")
        return items

    def create_session(self, channel_id: str, root_id: str) -> str:
        label = json.dumps({"channel_id": channel_id, "thread_root_id": root_id})
        metadata = json.dumps({"labels": {"omnigent.chat.v1": label}})
        response = self._send(
            "anna",
            "POST",
            "/v1/sessions",
            data={"metadata": metadata},
            files={"bundle": ("agent.tar.gz", _bundle_bytes(), "application/gzip")},
            allow=(200, 201),
        )
        session_id = response.json().get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("session create returned no session_id")
        return session_id

    def grant(self, session_id: str, username: str, level: int) -> None:
        self._send(
            "anna",
            "PUT",
            f"/v1/sessions/{_quote(session_id)}/permissions",
            json={"user_id": username, "level": level},
            allow=(200, 201, 204, 409),
        )

    def event(self, session_id: str, kind: str, data: dict[str, Any]) -> None:
        self._send(
            "anna",
            "POST",
            f"/v1/sessions/{_quote(session_id)}/events",
            json={"type": kind, "data": data},
            allow=(200, 201, 202, 204),
        )

    def pending_ids(self, session_id: str) -> set[str]:
        response = self._send("anna", "GET", f"/v1/sessions/{_quote(session_id)}")
        entries = response.json().get("pending_elicitations") or []
        return {str(entry.get("elicitation_id")) for entry in entries if isinstance(entry, dict)}

    def hold_approval(self, session_id: str, elicitation_id: str) -> int:
        """Long-poll the permission hook until someone answers or it times out."""
        payload = {
            "session_id": "claude_sess_demo",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/tmp",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {
                "command": "alembic upgrade head",
                "description": "Migration anwenden?",
            },
            "_omnigent_elicitation_id": elicitation_id,
        }
        response = self.http.post(
            f"{self.base_url}/v1/sessions/{_quote(session_id)}/hooks/permission-request",
            json=payload,
            headers={"Authorization": f"Bearer {self.tokens['anna']}"},
            timeout=_HOLD_TIMEOUT_S,
        )
        self._forget_cookies()
        if response.status_code >= 400:
            _raise_for(response)
        return response.status_code

    def _channel_id(self, name: str) -> str:
        sidebar = self.bootstrap(_ADMIN)
        for channel in sidebar.get("channels") or []:
            if isinstance(channel, dict) and channel.get("name") == name:
                return str(channel["id"])
        raise RuntimeError(f"channel {name} already exists but is not visible to {_ADMIN}")

    def _send(
        self,
        username: str,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        allow: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        response = self.http.request(
            method,
            f"{self.base_url}{path}",
            json=json,
            params=params,
            data=data,
            files=files,
            headers={"Authorization": f"Bearer {self.tokens[username]}"},
        )
        self._forget_cookies()
        if response.status_code not in allow:
            _raise_for(response)
        return response


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _raise_for(response: httpx.Response) -> None:
    """Raise without the request, so a bearer token cannot land in a traceback."""
    detail = ""
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = None
    if isinstance(body, dict):
        raw = body.get("error") or body.get("detail") or body.get("message") or ""
        detail = str(raw)[:200]
    path = urllib.parse.urlsplit(str(response.request.url)).path
    raise RuntimeError(
        f"{response.request.method} {path} -> {response.status_code} {detail}".rstrip()
    )


def linked_session_id(database_uri: str, root_message_id: str) -> str | None:
    """Return the session already linked to this thread root, if any."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from omnigent.db.db_models import SqlChatThread, current_workspace_id
    from omnigent.db.utils import get_or_create_engine

    with Session(get_or_create_engine(database_uri)) as session:
        row = session.execute(
            select(SqlChatThread).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == root_message_id,
            )
        ).scalar_one_or_none()
        if row is None or not row.session_id:
            return None
        return str(row.session_id)


def link_session(
    database_uri: str,
    *,
    root_message_id: str,
    channel_id: str,
    session_id: str,
    caller_id: str,
) -> None:
    """Point the thread at the session the way a finished agent start does."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from omnigent.db.db_models import SqlChatThread, current_workspace_id
    from omnigent.db.utils import get_or_create_engine, now_epoch_us

    now = now_epoch_us()
    with Session(get_or_create_engine(database_uri)) as session:
        row = session.execute(
            select(SqlChatThread).where(
                SqlChatThread.workspace_id == current_workspace_id(),
                SqlChatThread.root_message_id == root_message_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = SqlChatThread(
                root_message_id=root_message_id,
                channel_id=channel_id,
                reply_count=0,
                participants="[]",
                agent_state="active",
                updated_at=now,
            )
            session.add(row)
        if not row.session_id:
            row.session_id = session_id
        row.agent_handle = "agent"
        row.agent_state = "active"
        row.caller_id = caller_id
        row.channel_id = channel_id
        row.start_requested_at = row.start_requested_at or now
        row.updated_at = now
        session.commit()


def _has_agent_reply(api: DemoApi, channel_id: str, root_id: str) -> bool:
    items = api.thread_messages("anna", channel_id, root_id)
    return any(item.get("author_kind") == "omnigent_agent" for item in items)


def _wait_for_agent_reply(api: DemoApi, channel_id: str, root_id: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _has_agent_reply(api, channel_id, root_id):
            return
        time.sleep(0.2)
    raise RuntimeError("agent reply did not appear in the thread")


def seed(api: DemoApi, plan: SeedPlan, database_uri: str, *, hold: bool) -> str:
    """Create the demo content. Return ``pending`` or ``holding``."""
    validate_plan(plan)
    api.ensure_users()
    for username in plan.users:
        api.bootstrap(username)
    general = api.bootstrap(plan.admin)
    general_channels = general.get("channels") or []
    general_id = next(
        (str(item["id"]) for item in general_channels if item.get("name") == "general"),
        None,
    )
    if general_id is None:
        raise RuntimeError("bootstrap did not return #general")
    api.post(general_id, plan.pointer)

    channel_ids: dict[str, str] = {}
    for channel in plan.channels:
        channel_id = api.ensure_channel(channel)
        channel_ids[channel.name] = channel_id
        for message in channel.messages:
            api.post(channel_id, message)
        root_id = api.post(channel_id, channel.thread.root)
        for reply in channel.thread.replies:
            api.post(channel_id, reply, root_id)

    dm_id = api.open_dm(plan.direct)
    for message in plan.direct.messages:
        api.post(dm_id, message)

    agent_channel_id = channel_ids[plan.agent.channel]
    agent_root_id = api.post(agent_channel_id, plan.agent.root)
    session_id = linked_session_id(database_uri, agent_root_id)
    if session_id is None:
        session_id = api.create_session(agent_channel_id, agent_root_id)
        link_session(
            database_uri,
            root_message_id=agent_root_id,
            channel_id=agent_channel_id,
            session_id=session_id,
            caller_id="anna",
        )
    api.grant(session_id, "jens", _LEVEL_EDIT)
    api.grant(session_id, "bob", _LEVEL_READ)
    # The session transcript is append-only; send the reply only once per thread.
    if not _has_agent_reply(api, agent_channel_id, agent_root_id):
        api.event(
            session_id,
            "external_assistant_message",
            {
                "agent": "chat_agent",
                "text": plan.agent.reply_text,
                "response_id": plan.agent.response_id,
            },
        )
        api.event(
            session_id,
            "external_session_status",
            {"status": "idle", "response_id": plan.agent.response_id},
        )
        _wait_for_agent_reply(api, agent_channel_id, agent_root_id)
    api.event(
        session_id,
        "external_session_status",
        {"status": "waiting", "response_id": "resp_demo_waiting"},
    )
    if api.pending_ids(session_id):
        print(f"approval already pending session={session_id}")
        return "pending"
    if not hold:
        print(f"seeded session={session_id} without holding an approval")
        return "seeded"
    elicitation_id = new_elicitation_id()
    print(f"holding approval session={session_id} elicitation={elicitation_id}", flush=True)
    status = api.hold_approval(session_id, elicitation_id)
    print(f"approval hook returned {status}")
    return "holding"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the team-chat demo instance.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--database-uri", required=True)
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Keep the approval card open until it is answered or the hook times out.",
    )
    args = parser.parse_args(argv)
    assert_private_file(args.credentials)
    passwords = passwords_from_text(args.credentials.read_text(encoding="utf-8"))
    plan = demo_plan()
    api = DemoApi(args.base_url, passwords)
    try:
        seed(api, plan, args.database_uri, hold=args.hold)
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
