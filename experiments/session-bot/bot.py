"""Durable bot record and the transitions from bots/SPEC.md."""

from __future__ import annotations

import json
import re
import secrets
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUSES = (
    "idle",
    "session_open",
    "awaiting_approval",
    "implementing",
    "awaiting_review",
    "done",
    "blocked",
)
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
ID_RE = re.compile(r"^bot_[0-9a-f]{8}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REA_ARGV = (
    "npx -y rea-agents@latest analyze-javascript-application {target} --json"
)


class BotError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Bot:
    id: str
    name: str
    kind: str
    assignment: str
    status: str = "idle"
    memory: str = ""
    session_id: str = ""
    rea_target: str = ""
    rea_command: str = ""
    approval_reason: str = ""
    result: str = ""
    result_fresh: bool = False
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "assignment": self.assignment,
            "status": self.status,
            "memory": self.memory,
            "session_id": self.session_id,
            "rea_target": self.rea_target,
            "rea_command": self.rea_command,
            "approval_reason": self.approval_reason,
            "result": self.result,
            "result_fresh": self.result_fresh,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Bot:
        try:
            bot = cls(
                id=raw["id"],
                name=raw["name"],
                kind=raw["kind"],
                assignment=raw["assignment"],
                status=raw["status"],
                memory=raw["memory"],
                session_id=raw["session_id"],
                rea_target=raw["rea_target"],
                rea_command=raw["rea_command"],
                approval_reason=raw["approval_reason"],
                result=raw["result"],
                result_fresh=bool(raw["result_fresh"]),
                history=list(raw["history"]),
            )
        except (KeyError, TypeError) as exc:
            raise BotError("bot record is unreadable") from exc
        if bot.status not in STATUSES or not ID_RE.fullmatch(bot.id):
            raise BotError("bot record is unreadable")
        return bot

    def _event(self, event: str, detail: str = "") -> None:
        self.history.append({"at": _now(), "event": event, "detail": detail})

    def open_session(self, session_id: str) -> None:
        if self.status != "idle":
            raise BotError("a session can be opened only from idle")
        if not SESSION_RE.fullmatch(session_id):
            raise BotError(
                "session id must be 8 to 64 letters, digits, underscores, or hyphens"
            )
        self.session_id = session_id
        self.status = "session_open"
        self._event("session_opened", session_id)

    def close_session(self) -> None:
        if self.status != "session_open":
            raise BotError(
                "a session can be closed only while it is open and before a gate"
            )
        closed = self.session_id
        self.session_id = ""
        self.status = "idle"
        self._event("session_closed", closed)

    def prepare_rea(self, target: str) -> None:
        if self.status != "session_open":
            raise BotError("prepare-rea only while a session is open")
        path = Path(target).expanduser().resolve()
        if not path.is_dir():
            raise BotError("REA target must be an existing directory")
        self.rea_target = str(path)
        self.rea_command = REA_ARGV.format(target=shlex.quote(str(path)))
        self._event("rea_prepared", self.rea_target)

    def note(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            raise BotError("note is empty")
        if self.status not in ("session_open", "implementing"):
            raise BotError(
                "notes are only accepted while a session is open or work is in progress"
            )
        self.memory = f"{self.memory}\n{cleaned}" if self.memory else cleaned
        if self.status == "implementing":
            self.result = cleaned
            self.result_fresh = True
        self._event("noted", cleaned)

    def request_approval(self, reason: str) -> None:
        if self.status != "session_open":
            raise BotError("approval can be requested only while a session is open")
        if not self.session_id:
            raise BotError("approval needs a session")
        if not self.rea_command:
            raise BotError("approval needs a prepared REA command")
        cleaned = reason.strip()
        if not cleaned:
            raise BotError("approval needs a reason")
        self.approval_reason = cleaned
        self.status = "awaiting_approval"
        self._event("approval_requested", cleaned)

    def request_review(self) -> None:
        if self.status != "implementing":
            raise BotError("review can be requested only while implementing")
        if not self.result_fresh:
            raise BotError(
                "review needs a note written during this implementation round"
            )
        self.status = "awaiting_review"
        self._event("review_requested", self.result)

    def decide(self, approve: bool) -> None:
        if self.status == "awaiting_approval":
            if approve:
                self.status = "implementing"
                self.result = ""
                self.result_fresh = False
                self._event("approved", self.approval_reason)
                return
            self.status = "blocked"
            self._event("rejected", self.approval_reason)
            return
        if self.status == "awaiting_review":
            if approve:
                self.status = "done"
                self._event("review_approved", self.result)
                return
            self.status = "implementing"
            self.result_fresh = False
            self._event("review_rejected", self.result)
            return
        raise BotError("there is nothing to decide")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, bot_id: str) -> Path:
        if not ID_RE.fullmatch(bot_id):
            raise BotError("unknown bot")
        return self.root / f"{bot_id}.json"

    def create(self, name: str, assignment: str, kind: str = "rea") -> Bot:
        if kind != "rea":
            raise BotError("this prototype has one kind: rea")
        if not NAME_RE.fullmatch(name):
            raise BotError(
                "name must be 2 to 41 lowercase letters, digits, or hyphens"
            )
        text = assignment.strip()
        if not text or len(text) > 500:
            raise BotError("assignment must be 1 to 500 characters")
        bot = Bot(
            id=f"bot_{secrets.token_hex(4)}",
            name=name,
            kind="rea",
            assignment=text,
        )
        bot._event("created", text)
        self.save(bot)
        return bot

    def load(self, bot_id: str) -> Bot:
        path = self.path_for(bot_id)
        if not path.is_file():
            raise BotError("unknown bot")
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise BotError("bot record is unreadable") from exc
        if not isinstance(raw, dict):
            raise BotError("bot record is unreadable")
        return Bot.from_dict(raw)

    def save(self, bot: Bot) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(bot.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(bot.to_dict(), indent=2) + "\n")
        tmp.replace(path)
