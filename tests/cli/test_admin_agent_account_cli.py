"""``omnigent admin agent-account create|list|revoke`` on a scratch database."""

from __future__ import annotations

import json
import os
import plistlib
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlChatAgentAccount, SqlProject
from omnigent.db.utils import get_or_create_engine
from omnigent.server.agent_account_tokens import agent_account_limit, hash_agent_token
from tests.server.chat_testkit import ensure_live_user

OWNER = "jens@bloopark.de"
OTHER = "anna@bloopark.de"
SECRET = "test-admin-cli-agent-secret"
PROJECT = "cf256425327e40b7aac960d37086eb19"
FOREIGN = "ad2bc03dae8f47b98ce1bf5197881f7a"


@pytest.fixture()
def env(db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", SECRET)
    monkeypatch.delenv("OMNIGENT_AGENT_ACCOUNT_LIMIT", raising=False)
    ensure_live_user(db_uri, OWNER)
    ensure_live_user(db_uri, OTHER)
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(SqlProject(id=PROJECT, name="EQUent", user_id=OWNER, created_at=1))
        session.add(SqlProject(id=FOREIGN, name="FT Max", user_id=OTHER, created_at=1))
        session.commit()
    return db_uri


def _run(*args: str):
    from omnigent.cli import cli

    return CliRunner().invoke(cli, ["admin", "agent-account", *args], catch_exceptions=False)


def _create(db_uri: str, tmp_path: Path, handle: str, *extra: str):
    return _run(
        "create",
        "--database-uri",
        db_uri,
        "--owner",
        OWNER,
        "--handle",
        handle,
        "--display-name",
        f"Bot {handle}",
        "--out",
        str(tmp_path / f"{handle}.credentials"),
        *extra,
    )


def _active(db_uri: str) -> list[SqlChatAgentAccount]:
    with Session(get_or_create_engine(db_uri)) as session:
        return list(
            session.execute(
                select(SqlChatAgentAccount).where(SqlChatAgentAccount.revoked_at.is_(None))
            ).scalars()
        )


def _audit_lines(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "data" / "admin-audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_create_scoped_writes_0600_file_and_never_prints_token(env: str, tmp_path: Path) -> None:
    result = _create(env, tmp_path, "equent-simon", "--project", PROJECT)
    assert result.exit_code == 0, result.output
    path = tmp_path / "equent-simon.credentials"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cred = json.loads(path.read_text())
    assert cred["token"].startswith("ogc_")
    assert cred["token"] not in result.output
    assert cred["webhook_secret"] not in result.output
    assert cred["project_ids"] == [PROJECT]
    assert cred["owner_id"] == OWNER
    (row,) = _active(env)
    assert row.handle == "equent-simon"
    assert row.token_hash == hash_agent_token(cred["token"])
    (audit,) = _audit_lines(tmp_path)
    assert audit["action"] == "chat.agent_account.create"
    assert audit["outcome"] == "created"
    assert audit["account_id"] == row.id
    audit_path = tmp_path / "data" / "admin-audit.jsonl"
    raw = audit_path.read_text()
    assert cred["token"] not in raw and cred["webhook_secret"] not in raw
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600


def test_foreign_project_is_refused_and_nothing_created(env: str, tmp_path: Path) -> None:
    result = _create(env, tmp_path, "bad-scope", "--project", FOREIGN)
    assert result.exit_code != 0
    assert "project not found" in result.output
    assert _active(env) == []
    assert not (tmp_path / "bad-scope.credentials").exists()


def test_scope_or_unscoped_is_required(env: str, tmp_path: Path) -> None:
    assert _create(env, tmp_path, "no-scope").exit_code != 0
    both = _create(env, tmp_path, "both", "--project", PROJECT, "--unscoped")
    assert both.exit_code != 0
    assert _active(env) == []
    unscoped = _create(env, tmp_path, "full-bot", "--unscoped")
    assert unscoped.exit_code == 0, unscoped.output
    (row,) = _active(env)
    assert row.project_ids is None


def test_invalid_handle_and_dead_owner_are_refused(env: str, tmp_path: Path) -> None:
    assert _create(env, tmp_path, "Bad Handle", "--project", PROJECT).exit_code != 0
    dead = _run(
        "create",
        "--database-uri",
        env,
        "--owner",
        "ghost@example.com",
        "--handle",
        "ghost",
        "--display-name",
        "Ghost",
        "--unscoped",
        "--out",
        str(tmp_path / "ghost.credentials"),
    )
    assert dead.exit_code != 0
    assert "not a live user" in dead.output
    agent_owner = _run(
        "create",
        "--database-uri",
        env,
        "--owner",
        "agent:abc",
        "--handle",
        "nested",
        "--display-name",
        "Nested",
        "--unscoped",
        "--out",
        str(tmp_path / "n.credentials"),
    )
    assert agent_owner.exit_code != 0
    assert _active(env) == []


def test_missing_secret_is_refused(env: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_CHAT_AGENT_SECRET")
    result = _create(env, tmp_path, "nosecret", "--project", PROJECT)
    assert result.exit_code != 0
    assert "OMNIGENT_CHAT_AGENT_SECRET is not set" in result.output
    assert _active(env) == []


def test_existing_credentials_file_is_never_overwritten(env: str, tmp_path: Path) -> None:
    target = tmp_path / "taken.credentials"
    target.write_text("keep")
    result = _create(env, tmp_path, "taken", "--project", PROJECT)
    assert result.exit_code != 0
    assert target.read_text() == "keep"
    assert _active(env) == []


def test_duplicate_handle_is_refused(env: str, tmp_path: Path) -> None:
    assert _create(env, tmp_path, "dup", "--project", PROJECT).exit_code == 0
    again = _run(
        "create",
        "--database-uri",
        env,
        "--owner",
        OWNER,
        "--handle",
        "dup",
        "--display-name",
        "Dup",
        "--project",
        PROJECT,
        "--out",
        str(tmp_path / "dup2.credentials"),
    )
    assert again.exit_code != 0
    assert "handle already exists" in again.output
    assert not (tmp_path / "dup2.credentials").exists()


def test_default_limit_is_five(env: str, tmp_path: Path) -> None:
    for index in range(5):
        assert _create(env, tmp_path, f"bot-{index}", "--project", PROJECT).exit_code == 0
    sixth = _create(env, tmp_path, "bot-5", "--project", PROJECT)
    assert sixth.exit_code != 0
    assert "limit reached (5" in sixth.output
    assert not (tmp_path / "bot-5.credentials").exists()


def test_limit_is_configurable(env: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_ACCOUNT_LIMIT", "2")
    assert _create(env, tmp_path, "a1", "--project", PROJECT).exit_code == 0
    assert _create(env, tmp_path, "a2", "--project", PROJECT).exit_code == 0
    assert _create(env, tmp_path, "a3", "--project", PROJECT).exit_code != 0
    monkeypatch.setenv("OMNIGENT_AGENT_ACCOUNT_LIMIT", "7")
    assert _create(env, tmp_path, "a3", "--project", PROJECT).exit_code == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 5), ("abc", 5), ("0", 5), ("-3", 5), ("12", 12), ("100000", 100)],
)
def test_limit_parsing_never_fails_open(raw: str, expected: int, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_AGENT_ACCOUNT_LIMIT", raw)
    assert agent_account_limit() == expected


def test_list_shows_no_secrets_and_counts(env: str, tmp_path: Path) -> None:
    assert _create(env, tmp_path, "lister", "--project", PROJECT).exit_code == 0
    cred = json.loads((tmp_path / "lister.credentials").read_text())
    text = _run("list", "--database-uri", env, "--owner", OWNER)
    assert text.exit_code == 0, text.output
    assert "lister" in text.output
    assert f"# {OWNER}: 1/5 active" in text.output
    as_json = _run("list", "--database-uri", env, "--json")
    payload = json.loads(as_json.output)
    assert payload["limit"] == 5
    assert payload["accounts"][0]["project_ids"] == [PROJECT]
    for output in (text.output, as_json.output):
        assert cred["token"] not in output and cred["webhook_secret"] not in output


def test_revoke_by_handle(env: str, tmp_path: Path) -> None:
    assert _create(env, tmp_path, "gone", "--project", PROJECT).exit_code == 0
    wrong_owner = _run("revoke", "--database-uri", env, "--owner", OTHER, "gone", "--yes")
    assert wrong_owner.exit_code != 0
    assert len(_active(env)) == 1
    result = _run("revoke", "--database-uri", env, "--owner", OWNER, "gone", "--yes")
    assert result.exit_code == 0, result.output
    assert _active(env) == []
    assert [a["outcome"] for a in _audit_lines(tmp_path)] == ["created", "revoked"]


def test_revoke_asks_for_confirmation(env: str, tmp_path: Path) -> None:
    from omnigent.cli import cli

    assert _create(env, tmp_path, "keepme", "--project", PROJECT).exit_code == 0
    result = CliRunner().invoke(
        cli,
        ["admin", "agent-account", "revoke", "--database-uri", env, "--owner", OWNER, "keepme"],
        input="n\n",
    )
    assert result.exit_code != 0
    assert len(_active(env)) == 1


def _plist(tmp_path: Path, db_uri: str, mode: int) -> Path:
    path = tmp_path / "ai.omnigent.server.plist"
    path.write_bytes(
        plistlib.dumps(
            {
                "ProgramArguments": [
                    "python",
                    "-m",
                    "omnigent.cli",
                    "server",
                    "--database-uri",
                    db_uri,
                ],
                "EnvironmentVariables": {"OMNIGENT_CHAT_AGENT_SECRET": SECRET, "OTHER_X": "x"},
            }
        )
    )
    os.chmod(path, mode)
    return path


def test_launchd_plist_supplies_secret_and_database(env: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_CHAT_AGENT_SECRET")
    plist = _plist(tmp_path, env, 0o600)
    result = _run(
        "create",
        "--launchd-plist",
        str(plist),
        "--owner",
        OWNER,
        "--handle",
        "from-plist",
        "--display-name",
        "From plist",
        "--project",
        PROJECT,
        "--out",
        str(tmp_path / "from-plist.credentials"),
    )
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    cred = json.loads((tmp_path / "from-plist.credentials").read_text())
    (row,) = _active(env)
    assert row.token_hash == hash_agent_token(cred["token"])
    assert "OTHER_X" not in os.environ
    monkeypatch.delenv("OMNIGENT_CHAT_AGENT_SECRET", raising=False)


def test_group_readable_plist_is_refused(env: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_CHAT_AGENT_SECRET")
    plist = _plist(tmp_path, env, 0o644)
    result = _run("list", "--launchd-plist", str(plist))
    assert result.exit_code != 0
    assert "chmod 600" in result.output


def test_missing_sqlite_file_is_not_created(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "data"))
    missing = tmp_path / "nope.db"
    result = _run("list", "--database-uri", f"sqlite:///{missing}")
    assert result.exit_code != 0
    assert not missing.exists()
