"""MCP agent onboarding: guide, answers, review, and the write gate.

Chat delivery is not covered here. Those cases live in
``test_mcp_onboarding_chat_later.py`` and stay skipped until team chat ships.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.orm import Session

from omnigent.db.db_models import SqlProject
from omnigent.db.utils import get_or_create_engine
from omnigent.server.agent_onboarding import (
    Caller,
    OnboardingError,
    OnboardingService,
)
from omnigent.server.agent_onboarding_guide import load_guide
from omnigent.server.agent_onboarding_secrets import contains_secret
from omnigent.server.feature_flags import FEATURES_ENV_VAR, Feature, resolve_feature_flags
from omnigent.stores.chat_store import AccountStatus
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.onboarding_store import OnboardingStore
from tests.server.chat_testkit import (
    AGENT_SECRET,
    JENS,
    build_chat_app,
    ensure_live_user,
    rpc,
    tool_payload,
    tools_call,
)

_GUIDE = """
version: "0.1"
integration_branch: staging
orchestrator_handles:
  - onboarding-orchestrator
merge_exceptions:
  trading-bot: staging
rules:
  - "Findings come back from onboarding_status."
questions:
  - id: integration_branch
    prompt: "Which integration branch did you verify?"
    required: true
    matches: integration_branch
  - id: list_projects_count
    prompt: "How many projects does this account see?"
    required: true
    matches_const: "1"
  - id: ack
    prompt: "Confirm the rules."
    required: true
delta_questions:
  - id: ack_delta
    prompt: "Confirm the guide change."
    required: true
timeouts:
  remind_1_us: 86400000000
  remind_2_us: 172800000000
  stall_us: 259200000000
  owner_digest_us: 86400000000
  owner_wait_us: 259200000000
client_retry_s: [10, 60, 300]
stall_after_s: 1800
"""

_FAST_CLOCK = (
    _GUIDE.replace(
        "remind_1_us: 86400000000",
        "remind_1_us: 1000",
    )
    .replace(
        "remind_2_us: 172800000000",
        "remind_2_us: 2000",
    )
    .replace(
        "stall_us: 259200000000",
        "stall_us: 3000",
    )
    .replace(
        "owner_digest_us: 86400000000",
        "owner_digest_us: 1000",
    )
    .replace(
        "owner_wait_us: 259200000000",
        "owner_wait_us: 5000",
    )
)

_FAKE_SECRET = "ogc_" + "notareal" + "token"
_NOW = 1_000_000_000


def _answers(*, branch: str = "staging", ack: str = "yes") -> dict[str, str]:
    return {
        "integration_branch": branch,
        "list_projects_count": "1",
        "ack": ack,
    }


def _write_guide(directory: Path, project_id: str, text: str = _GUIDE) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{project_id}.yaml").write_text(text, encoding="utf-8")


def _project(db_uri: str, project_id: str, name: str) -> None:
    ensure_live_user(db_uri, JENS)
    with Session(get_or_create_engine(db_uri)) as session:
        session.add(
            SqlProject(id=project_id, name=name, user_id=JENS, created_at=1, updated_at=None)
        )
        session.commit()


def _account(db_uri: str, handle: str, project_ids: tuple[str, ...] | None):
    result = SqlAlchemyChatStore(db_uri).create_agent_account(
        JENS,
        handle,
        handle,
        project_ids=project_ids,
    )
    assert result.status is AccountStatus.CREATED, result.status
    assert result.issue is not None
    return result.issue.account, result.issue.token


def _service(db_uri: str, guides: Path, host: str = "unknown") -> OnboardingService:
    return OnboardingService(db_uri, guides_dir=str(guides), host_probe=lambda _owner: host)


def _owner() -> Caller:
    return Caller(JENS, "user", False)


def _agent(account) -> Caller:
    return Caller(account.principal_id, "agent_account", False)


def _tool_error(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    assert result.get("isError") is True, result
    structured = result.get("structuredContent") or {}
    return {
        "code": structured.get("code"),
        "text": result["content"][0]["text"],
    }


def test_secret_scan_rejects_tokens_and_keeps_hex_digests() -> None:
    digest = hashlib.sha256(b"guide").hexdigest()
    assert contains_secret(_FAKE_SECRET) is True
    assert contains_secret({"note": "-----BEGIN PRIVATE KEY-----"}) is True
    assert contains_secret("sk-" + "a" * 12) is True
    assert contains_secret({_FAKE_SECRET: "yes"}) is True
    assert contains_secret({"q": {_FAKE_SECRET: "yes"}}) is True
    assert contains_secret(digest) is False
    assert contains_secret("staging") is False


def test_guide_hash_is_the_file_bytes_and_lookup_prefers_the_override(tmp_path: Path) -> None:
    project_id = uuid.uuid4().hex
    directory = tmp_path / "guides"
    _write_guide(directory, project_id)
    guide = load_guide(project_id, guides_dir=str(directory))
    raw = (directory / f"{project_id}.yaml").read_bytes()
    assert guide.guide_hash == hashlib.sha256(raw).hexdigest()
    assert guide.orchestrator_handles == ("onboarding-orchestrator",)
    assert guide.client_retry_s == (10, 60, 300)
    missing = load_guide(uuid.uuid4().hex, guides_dir=str(directory))
    assert missing.source == "default.yaml"
    bundled = (
        Path(__file__).resolve().parents[2]
        / "omnigent"
        / "resources"
        / "onboarding"
        / "default.yaml"
    )
    assert missing.guide_hash == hashlib.sha256(bundled.read_bytes()).hexdigest()


def test_agent_onboarding_flag_stays_off_and_out_of_the_frontend_snapshot() -> None:
    flags = resolve_feature_flags({})
    assert flags.enabled(Feature.AGENT_ONBOARDING) is False
    assert "agent_onboarding" not in flags.frontend_dict()
    enabled = resolve_feature_flags({FEATURES_ENV_VAR: "agent_onboarding"})
    assert enabled.enabled(Feature.AGENT_ONBOARDING) is True
    assert "agent_onboarding" not in enabled.frontend_dict()


def test_revision_is_the_only_child_of_the_previous_head() -> None:
    versions = Path(__file__).resolve().parents[2] / "omnigent" / "db" / "migrations" / "versions"
    text = (versions / "ob1a2b3c4d5e_add_agent_onboarding.py").read_text(encoding="utf-8")
    assert 'revision: str = "ob1a2b3c4d5e"' in text
    assert 'down_revision: str | None = "ps1a2b3c4d5e"' in text
    parents = [
        path.name
        for path in versions.glob("*.py")
        if 'down_revision: str | None = "ps1a2b3c4d5e"' in path.read_text(encoding="utf-8")
        or 'down_revision: str | tuple[str, ...] | None = "ps1a2b3c4d5e"'
        in path.read_text(encoding="utf-8")
    ]
    assert parents == ["ob1a2b3c4d5e_add_agent_onboarding.py"]


def test_happy_path_reaches_active_without_storing_a_secret(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "onboarding-project")
    bot, _token = _account(db_uri, "onboarding-testbot", (project_id,))
    orch, _orch_token = _account(db_uri, "onboarding-orchestrator", (project_id,))
    service = _service(db_uri, guides)
    started = service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    assert started["status"] == "invited"
    assert started["token_prefix_ok"] is True
    assert "answers" not in started
    again = service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    assert again["idempotent"] is True
    reading = service.status(_agent(bot), account_ref=None, project_id=project_id, now_us=_NOW)
    assert reading["status"] == "access_ok"
    assert "owner_notices" not in reading
    guide = service.get_guide(_agent(bot), project_id)
    submitted = service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 1,
    )
    assert submitted["status"] == "submitted"
    assert submitted["findings"] == []
    assert submitted["answers_stored"] is True
    assert _FAKE_SECRET not in json.dumps(submitted)
    same = service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 2,
    )
    assert same["idempotent"] is True
    reviewed = service.review(
        _agent(orch),
        account_ref=bot.handle,
        project_id=project_id,
        verdict="PASS",
        findings=[],
        reviewer="deepseek",
        fallback_for_claude=True,
        now_us=_NOW + 3,
    )
    assert reviewed["status"] == "awaiting_owner"
    assert reviewed["review_passed"] is True
    with pytest.raises(OnboardingError, match="forbidden"):
        service.approve(
            _agent(bot), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 4
        )
    approved = service.approve(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 5
    )
    assert approved["status"] == "active"
    assert approved["approved_by"] == JENS
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.review is not None
    assert stored.review["follow_up"] == "opus_rereview"
    assert stored.answers == _answers()
    audits = OnboardingStore(db_uri).audits(stored.id)
    assert "approve_forbidden" in {entry.action for entry in audits}
    detail = json.dumps([entry.detail for entry in audits])
    assert "yes" not in detail
    assert _FAKE_SECRET not in detail


def test_secret_submit_stores_nothing_and_can_be_replaced(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "secret-project")
    bot, _token = _account(db_uri, "secret-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    service.status(_agent(bot), account_ref=None, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    with pytest.raises(OnboardingError, match="secret_detected"):
        service.submit(
            _agent(bot),
            project_id=project_id,
            guide_hash=guide["guide_hash"],
            answers=_answers(ack=_FAKE_SECRET),
            now_us=_NOW + 1,
        )
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.status == "rejected_auto"
    assert stored.answers is None
    owner_view = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 1
    )
    assert any(item["code"] == "secret_detected" for item in owner_view["owner_notices"])
    assert _FAKE_SECRET not in json.dumps(owner_view)
    bot_view = service.status(
        _agent(bot), account_ref=None, project_id=project_id, now_us=_NOW + 1
    )
    assert "owner_notices" not in bot_view
    replaced = service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 2,
    )
    assert replaced["status"] == "submitted"


def test_secret_in_answer_key_is_rejected_and_not_stored(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "secret-key-project")
    bot, _token = _account(db_uri, "secret-key-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    service.status(_agent(bot), account_ref=None, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    with pytest.raises(OnboardingError, match="secret_detected"):
        service.submit(
            _agent(bot),
            project_id=project_id,
            guide_hash=guide["guide_hash"],
            answers={**_answers(), _FAKE_SECRET: "yes"},
            now_us=_NOW + 1,
        )
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.status == "rejected_auto"
    assert stored.answers is None
    owner_view = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 1
    )
    assert any(item["code"] == "secret_detected" for item in owner_view["owner_notices"])
    assert _FAKE_SECRET not in json.dumps(owner_view)


def test_incomplete_and_guide_mismatch_leave_the_status_unchanged(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "incomplete-project")
    bot, _token = _account(db_uri, "incomplete-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    with pytest.raises(OnboardingError, match="guide_mismatch"):
        service.submit(
            _agent(bot),
            project_id=project_id,
            guide_hash="0" * 64,
            answers=_answers(),
            now_us=_NOW,
        )
    with pytest.raises(OnboardingError, match="incomplete: ack"):
        service.submit(
            _agent(bot),
            project_id=project_id,
            guide_hash=guide["guide_hash"],
            answers={"integration_branch": "staging", "list_projects_count": "1"},
            now_us=_NOW,
        )
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.status == "invited"
    assert stored.answers is None


def test_plausibility_finding_does_not_echo_the_answer(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "plausibility-project")
    bot, _token = _account(db_uri, "plausibility-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    submitted = service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(branch="mainline"),
        now_us=_NOW,
    )
    assert submitted["status"] == "submitted"
    assert submitted["findings"] == [
        {
            "code": "plausibility",
            "field": "integration_branch",
            "message": "integration_branch does not match the guide",
        }
    ]
    assert "mainline" not in json.dumps(submitted["findings"])


def test_third_rework_blocks_and_scope_mismatch_does_not_suspend(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    other_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "rework-project")
    _project(db_uri, other_id, "other-project")
    bot, _token = _account(db_uri, "rework-bot", (project_id, other_id))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    viewed = service.status(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    assert viewed["status"] == "invited"
    assert any(item["code"] == "scope_mismatch" for item in viewed["owner_notices"])
    assert viewed["status"] != "suspended"
    guide = service.get_guide(_owner(), project_id)
    for count in (1, 2, 3):
        service.submit(
            _agent(bot),
            project_id=project_id,
            guide_hash=guide["guide_hash"],
            answers=_answers(),
            now_us=_NOW + count,
        )
        reviewed = service.review(
            _owner(),
            account_ref=bot.handle,
            project_id=project_id,
            verdict="REWORK",
            findings=[f"round {count}"],
            reviewer="deepseek",
            fallback_for_claude=False,
            now_us=_NOW + 1 + count,
        )
    assert reviewed["status"] == "blocked"
    assert reviewed["rework_count"] == 3
    assert any(item["code"] == "blocked" for item in reviewed["owner_notices"])
    with pytest.raises(OnboardingError, match="invalid_status"):
        service.review(
            _owner(),
            account_ref=bot.handle,
            project_id=project_id,
            verdict="REWORK",
            findings=["again"],
            reviewer="deepseek",
            fallback_for_claude=False,
            now_us=_NOW + 10,
        )
    suspended = service.suspend(
        _owner(),
        account_ref=bot.handle,
        project_id=project_id,
        reason="scope is wider than the project",
        now_us=_NOW + 11,
    )
    assert suspended["status"] == "suspended"


def test_rework_resubmit_reset_and_import(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "reset-project")
    bot, _token = _account(db_uri, "reset-bot", (project_id,))
    imported, _imported_token = _account(db_uri, "trading-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 1,
    )
    service.review(
        _owner(),
        account_ref=bot.handle,
        project_id=project_id,
        verdict="REWORK",
        findings=["name the branch"],
        reviewer="deepseek",
        fallback_for_claude=False,
        now_us=_NOW + 2,
    )
    resubmitted = service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 3,
    )
    assert resubmitted["status"] == "submitted"
    assert "idempotent" not in resubmitted
    reset = service.reset(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 4)
    assert reset["status"] == "invited"
    assert reset["generation"] == 2
    assert reset["answers_stored"] is False
    assert reset["archive"][0]["status"] == "submitted"
    assert reset["archive"][0]["answers_hash"]
    assert "yes" not in json.dumps(reset["archive"])
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.opened_at == _NOW + 4
    assert stored.rework_count == 0
    assert stored.answers is None
    brought = service.import_existing(
        _owner(), account_ref=imported.handle, project_id=project_id, now_us=_NOW
    )
    assert brought["status"] == "refresh_required"
    assert brought["delta"] is True
    assert brought["merge_exception"] == "staging"
    assert any(item["code"] == "delta" for item in brought["bot_notices"])
    again = service.import_existing(
        _owner(), account_ref=imported.handle, project_id=project_id, now_us=_NOW + 1
    )
    assert again["idempotent"] is True
    delta = service.submit(
        _agent(imported),
        project_id=project_id,
        guide_hash=brought["guide_hash"],
        answers={"ack_delta": "yes"},
        now_us=_NOW + 2,
    )
    assert delta["status"] == "awaiting_owner"


def test_clock_stalls_and_owner_digest_without_a_scheduler(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id, _FAST_CLOCK)
    _project(db_uri, project_id, "clock-project")
    bot, _token = _account(db_uri, "clock-bot", (project_id,))
    service = _service(db_uri, guides)
    with pytest.raises(OnboardingError, match="forbidden"):
        service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    jumped = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 2000
    )
    codes = [item["code"] for item in jumped["bot_notices"]]
    assert "remind_48" in codes
    assert "remind_24" not in codes
    stalled = service.status(
        _agent(bot), account_ref=None, project_id=project_id, now_us=_NOW + 3000
    )
    assert stalled["status"] == "stalled"
    assert "owner_notices" not in stalled
    owner = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 3000
    )
    assert any(item["code"] == "stalled" for item in owner["owner_notices"])
    waiting, _waiting_token = _account(db_uri, "waiting-bot", (project_id,))
    service.start(_owner(), account_ref=waiting.handle, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(waiting), project_id)
    service.submit(
        _agent(waiting),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 1,
    )
    service.review(
        _owner(),
        account_ref=waiting.handle,
        project_id=project_id,
        verdict="PASS",
        findings=[],
        reviewer="deepseek",
        fallback_for_claude=False,
        now_us=_NOW + 2,
    )
    digest = service.status(
        _owner(), account_ref=waiting.handle, project_id=project_id, now_us=_NOW + 2 + 1000
    )
    assert any(item["code"] == "owner_digest" for item in digest["owner_notices"])
    hidden = service.status(
        _agent(waiting), account_ref=None, project_id=project_id, now_us=_NOW + 2 + 1000
    )
    assert "owner_notices" not in hidden
    assert "owner_digest" not in json.dumps(hidden)


def test_active_guide_change_requires_refresh_and_invited_does_not(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "drift-project")
    bot, _token = _account(db_uri, "drift-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    original = service.get_guide(_agent(bot), project_id)["guide_hash"]
    _write_guide(guides, project_id, _GUIDE + "\n# edited\n")
    still = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 1
    )
    assert still["status"] == "access_ok"
    assert still["guide_hash"] == original
    service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=load_guide(project_id, guides_dir=str(guides)).guide_hash,
        answers=_answers(),
        now_us=_NOW + 2,
    )
    service.review(
        _owner(),
        account_ref=bot.handle,
        project_id=project_id,
        verdict="PASS",
        findings=[],
        reviewer="deepseek",
        fallback_for_claude=False,
        now_us=_NOW + 3,
    )
    service.approve(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 4)
    _write_guide(guides, project_id, _GUIDE + "\n# edited again\n")
    drifted = service.status(_agent(bot), account_ref=None, project_id=project_id, now_us=_NOW + 5)
    assert drifted["status"] == "refresh_required"
    assert drifted["delta"] is True
    assert any(item["code"] == "delta" for item in drifted["bot_notices"])
    owner = service.status(
        _owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW + 5
    )
    assert any(item["code"] == "guide_changed" for item in owner["owner_notices"])


def test_hidden_projects_and_unscoped_agents_are_not_found(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    project_id = uuid.uuid4().hex
    foreign_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _write_guide(guides, foreign_id)
    _project(db_uri, project_id, "visible-project")
    _project(db_uri, foreign_id, "foreign-project")
    bot, _token = _account(db_uri, "scoped-bot", (project_id,))
    _account(db_uri, "unscoped-bot", None)
    service = _service(db_uri, guides)
    with pytest.raises(OnboardingError, match="not_found"):
        service.get_guide(_agent(bot), foreign_id)
    unscoped = OnboardingStore(db_uri).account_by_ref("unscoped-bot")
    assert unscoped is not None
    with pytest.raises(OnboardingError, match="not_found"):
        service.get_guide(_agent(unscoped), project_id)
    stranger = Caller("stranger@example.com", "user", False)
    with pytest.raises(OnboardingError, match="not_found"):
        service.start(stranger, account_ref=bot.handle, project_id=project_id)
    with pytest.raises(OnboardingError, match="not_found"):
        service.import_existing(
            _owner(), account_ref=bot.handle, project_id=foreign_id, now_us=_NOW
        )
    assert OnboardingStore(db_uri).get(bot.id, foreign_id) is None
    with pytest.raises(OnboardingError, match="not_found"):
        service.import_existing(
            _owner(), account_ref=unscoped.handle, project_id=project_id, now_us=_NOW
        )
    assert OnboardingStore(db_uri).list_for_account(unscoped.id) == ()


def test_access_failed_and_offline_host_keep_invited(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "offline-project")
    bot, _token = _account(db_uri, "offline-bot", (project_id,))
    service = _service(db_uri, guides, host="offline")
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    viewed = service.status(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    assert viewed["status"] == "invited"
    assert viewed["host"] == "offline"
    failed = service.review(
        _owner(),
        account_ref=bot.handle,
        project_id=project_id,
        verdict="ACCESS_FAILED",
        findings=[],
        reviewer="deepseek",
        fallback_for_claude=False,
        now_us=_NOW + 1,
    )
    assert failed["status"] == "access_failed"
    assert any(item["code"] == "access_failed" for item in failed["owner_notices"])
    again = service.review(
        _owner(),
        account_ref=bot.handle,
        project_id=project_id,
        verdict="ACCESS_FAILED",
        findings=["rotate"],
        reviewer="deepseek",
        fallback_for_claude=False,
        now_us=_NOW + 2,
    )
    assert again["idempotent"] is True


def test_review_text_with_a_secret_is_not_stored(
    db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    _project(db_uri, project_id, "review-secret-project")
    bot, _token = _account(db_uri, "review-secret-bot", (project_id,))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_id, now_us=_NOW)
    guide = service.get_guide(_agent(bot), project_id)
    service.submit(
        _agent(bot),
        project_id=project_id,
        guide_hash=guide["guide_hash"],
        answers=_answers(),
        now_us=_NOW + 1,
    )
    with pytest.raises(OnboardingError, match="secret_detected"):
        service.review(
            _owner(),
            account_ref=bot.handle,
            project_id=project_id,
            verdict="REWORK",
            findings=[_FAKE_SECRET],
            reviewer="deepseek",
            fallback_for_claude=False,
            now_us=_NOW + 2,
        )
    stored = OnboardingStore(db_uri).get(bot.id, project_id)
    assert stored is not None
    assert stored.status == "submitted"
    assert _FAKE_SECRET not in json.dumps(stored.findings)


@pytest_asyncio.fixture()
async def onboarding_client(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, str, str, str]]:
    """App with agent_onboarding on and team chat off."""
    del runtime_init
    project_id = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_id)
    monkeypatch.setenv(FEATURES_ENV_VAR, "agent_onboarding")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_GUIDES_DIR", str(guides))
    _project(db_uri, project_id, "mcp-project")
    _bot, bot_token = _account(db_uri, "onboarding-testbot", (project_id,))
    _orch, orch_token = _account(db_uri, "onboarding-orchestrator", (project_id,))
    _idle, idle_token = _account(db_uri, "idle-bot", (project_id,))
    app = build_chat_app(db_uri, tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project_id, bot_token, orch_token, idle_token


async def test_flag_off_hides_onboarding_tools(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del runtime_init
    monkeypatch.setenv(FEATURES_ENV_VAR, "")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    app = build_chat_app(db_uri, tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await rpc(client, "tools/list", user=JENS)
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        assert not any(name.startswith("onboarding_") for name in names)
        resources = await rpc(client, "resources/list", user=JENS)
        assert resources.json()["result"] == {"resources": []}
        initialized = await rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}},
            user=JENS,
        )
        assert "resources" not in initialized.json()["result"]["capabilities"]
        failed = await tools_call(
            client,
            "onboarding_status",
            {"project_id": "missing"},
            user=JENS,
        )
    error = _tool_error(failed)
    assert error["code"] == "not_found"


async def test_mcp_flow_gates_writes_until_the_owner_approves(
    onboarding_client: tuple[httpx.AsyncClient, str, str, str, str],
) -> None:
    client, project_id, bot_token, orch_token, idle_token = onboarding_client
    listed = await rpc(client, "tools/list", user=JENS, protocol="2025-11-25")
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert "onboarding_submit_answers" in names
    assert "chat_post" not in names
    initialized = await rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-11-25", "capabilities": {}},
        user=JENS,
    )
    assert initialized.json()["result"]["capabilities"]["resources"] == {"listChanged": False}
    resources = await rpc(client, "resources/list", token=bot_token, protocol="2025-11-25")
    assert resources.json()["result"]["resources"][0]["uri"] == "omnigent://onboarding/guide"
    guide_read = await rpc(
        client,
        "resources/read",
        {"uri": f"omnigent://onboarding/{project_id}"},
        token=bot_token,
        protocol="2025-11-25",
    )
    guide_body = json.loads(guide_read.json()["result"]["contents"][0]["text"])
    assert guide_body["guide_hash"]
    assert "default.yaml" not in guide_read.text
    hidden = await rpc(
        client,
        "resources/read",
        {"uri": "omnigent://onboarding/not-a-project"},
        token=bot_token,
        protocol="2025-11-25",
    )
    assert hidden.json()["error"]["code"] == -32002

    dry = await tools_call(
        client,
        "onboarding_submit_answers",
        {
            "project_id": project_id,
            "guide_hash": guide_body["guide_hash"],
            "answers": _answers(ack=_FAKE_SECRET),
        },
        token=bot_token,
    )
    preview = tool_payload(dry)
    assert preview["dry_run"] is True
    assert "answers" not in preview["body"]
    assert _FAKE_SECRET not in dry.text

    started = tool_payload(
        await tools_call(
            client,
            "onboarding_start",
            {"account": "onboarding-testbot", "project_id": project_id, "confirm": True},
            user=JENS,
        )
    )
    assert started["status"] == "invited"
    current = tool_payload(
        await tools_call(
            client,
            "onboarding_status",
            {"project_id": project_id},
            token=bot_token,
        )
    )
    assert current["status"] == "access_ok"
    assert "owner_notices" not in current
    blocked = _tool_error(
        await tools_call(
            client,
            "create_session",
            {"agent_name": "missing-agent"},
            token=bot_token,
        )
    )
    assert blocked["code"] == "onboarding_required"
    idle = _tool_error(
        await tools_call(
            client,
            "create_session",
            {"agent_name": "missing-agent"},
            token=idle_token,
        )
    )
    assert idle["code"] != "onboarding_required"

    submitted = tool_payload(
        await tools_call(
            client,
            "onboarding_submit_answers",
            {
                "project_id": project_id,
                "guide_hash": guide_body["guide_hash"],
                "answers": _answers(),
                "confirm": True,
            },
            token=bot_token,
        )
    )
    assert submitted["status"] == "submitted"
    reviewed = tool_payload(
        await tools_call(
            client,
            "onboarding_review",
            {
                "account": "onboarding-testbot",
                "project_id": project_id,
                "verdict": "PASS",
                "reviewer": "deepseek",
                "fallback_for_claude": True,
                "confirm": True,
            },
            token=orch_token,
        )
    )
    assert reviewed["status"] == "awaiting_owner"
    assert reviewed["review_passed"] is True
    forbidden = _tool_error(
        await tools_call(
            client,
            "onboarding_approve",
            {"account": "onboarding-testbot", "project_id": project_id, "confirm": True},
            token=bot_token,
        )
    )
    assert forbidden["code"] == "forbidden"
    approved = tool_payload(
        await tools_call(
            client,
            "onboarding_approve",
            {"account": "onboarding-testbot", "project_id": project_id, "confirm": True},
            user=JENS,
        )
    )
    assert approved["status"] == "active"
    opened = _tool_error(
        await tools_call(
            client,
            "create_session",
            {"agent_name": "missing-agent"},
            token=bot_token,
        )
    )
    assert opened["code"] != "onboarding_required"


async def test_offline_host_blocks_session_create_before_onboarding_required(
    onboarding_client: tuple[httpx.AsyncClient, str, str, str, str],
    db_uri: str,
) -> None:
    client, project_id, bot_token, _orch_token, _idle_token = onboarding_client
    await tools_call(
        client,
        "onboarding_start",
        {"account": "onboarding-testbot", "project_id": project_id, "confirm": True},
        user=JENS,
    )
    store = OnboardingStore(db_uri)
    account = store.account_by_ref("onboarding-testbot")
    assert account is not None
    record = store.get(account.id, project_id)
    assert record is not None
    record.host_check = "offline"
    store.save(record)
    blocked = _tool_error(
        await tools_call(
            client,
            "create_session",
            {"agent_name": "missing-agent"},
            token=bot_token,
        )
    )
    assert blocked["code"] == "host_offline"


async def test_write_gate_follows_the_named_project(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-active row blocks that project, not the account's other project."""
    del runtime_init
    project_p = uuid.uuid4().hex
    project_q = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_p)
    _write_guide(guides, project_q)
    monkeypatch.setenv(FEATURES_ENV_VAR, "agent_onboarding")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    monkeypatch.setenv("OMNIGENT_ONBOARDING_GUIDES_DIR", str(guides))
    _project(db_uri, project_p, "gate-p")
    _project(db_uri, project_q, "gate-q")
    bot, bot_token = _account(db_uri, "gate-bot", (project_p, project_q))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_p, now_us=_NOW)
    service.start(_owner(), account_ref=bot.handle, project_id=project_q, now_us=_NOW)
    store = OnboardingStore(db_uri)
    active = store.get(bot.id, project_p)
    other = store.get(bot.id, project_q)
    assert active is not None and other is not None
    active.status = "active"
    other.status = "refresh_required"
    store.save(active)
    store.save(other)
    filed = SqlAlchemyConversationStore(db_uri).create_conversation(
        title="filed-in-p", project_id=project_p
    )
    app = build_chat_app(db_uri, tmp_path)
    transport = httpx.ASGITransport(app=app)

    async def call(client: httpx.AsyncClient, name: str, arguments: dict) -> dict[str, object]:
        return _tool_error(await tools_call(client, name, arguments, token=bot_token))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_p}
        )
        blocked = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_q}
        )
        unnamed = await call(client, "create_session", {"agent_name": "missing-agent"})
        foreign = await call(
            client,
            "create_session",
            {"agent_name": "missing-agent", "project_id": uuid.uuid4().hex},
        )
        filed_response = await tools_call(
            client,
            "send_message",
            {"session_id": filed.id, "text": "hello"},
            token=bot_token,
        )
        other.host_check = "offline"
        store.save(other)
        offline = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_q}
        )
        open_while_other_offline = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_p}
        )
        other.status = "suspended"
        other.host_check = None
        store.save(other)
        still_open = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_p}
        )
        suspended = await call(
            client, "create_session", {"agent_name": "missing-agent", "project_id": project_q}
        )
    filed_payload = filed_response.json()
    assert "error" not in filed_payload
    filed_result = filed_payload["result"]
    if filed_result.get("isError") is True:
        assert (filed_result.get("structuredContent") or {}).get("code") != "onboarding_required"
    assert allowed["code"] not in {"onboarding_required", "suspended", "host_offline"}
    assert blocked["code"] == "onboarding_required"
    assert unnamed["code"] == "onboarding_required"
    assert foreign["code"] == "onboarding_required"
    assert offline["code"] == "host_offline"
    assert open_while_other_offline["code"] != "host_offline"
    assert still_open["code"] not in {"suspended", "onboarding_required"}
    assert suspended["code"] == "suspended"


def _gate_code(response: httpx.Response) -> str | None:
    """Return the tool error code, or ``None`` when the call was not refused."""
    payload = response.json()
    assert "error" not in payload, payload
    result = payload["result"]
    if result.get("isError") is not True:
        return None
    return (result.get("structuredContent") or {}).get("code")


async def test_write_gate_follows_the_session_root_project(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session write is gated on its root's project; extra project args are ignored."""
    del runtime_init
    project_p = uuid.uuid4().hex
    project_q = uuid.uuid4().hex
    guides = tmp_path / "guides"
    _write_guide(guides, project_p)
    _write_guide(guides, project_q)
    monkeypatch.setenv(FEATURES_ENV_VAR, "agent_onboarding")
    monkeypatch.setenv("OMNIGENT_CHAT_AGENT_SECRET", AGENT_SECRET)
    monkeypatch.setenv("OMNIGENT_ONBOARDING_ALLOW_CLOCK", "1")
    monkeypatch.setenv("OMNIGENT_ONBOARDING_GUIDES_DIR", str(guides))
    _project(db_uri, project_p, "root-p")
    _project(db_uri, project_q, "root-q")
    bot, bot_token = _account(db_uri, "root-bot", (project_p, project_q))
    service = _service(db_uri, guides)
    service.start(_owner(), account_ref=bot.handle, project_id=project_p, now_us=_NOW)
    service.start(_owner(), account_ref=bot.handle, project_id=project_q, now_us=_NOW)
    store = OnboardingStore(db_uri)
    active = store.get(bot.id, project_p)
    pending = store.get(bot.id, project_q)
    assert active is not None and pending is not None
    active.status = "active"
    pending.status = "refresh_required"
    store.save(active)
    store.save(pending)
    conversations = SqlAlchemyConversationStore(db_uri)
    root_q = conversations.create_conversation(title="root-in-q", project_id=project_q)
    root_p = conversations.create_conversation(title="root-in-p", project_id=project_p)
    child_filed_p_under_q = conversations.create_conversation(
        title="child-under-q",
        parent_conversation_id=root_q.id,
        project_id=project_p,
    )
    app = build_chat_app(db_uri, tmp_path)
    transport = httpx.ASGITransport(app=app)

    async def code(client: httpx.AsyncClient, name: str, arguments: dict) -> str | None:
        return _gate_code(await tools_call(client, name, arguments, token=bot_token))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # send_message into a Q-root session
        into_q = await code(client, "send_message", {"session_id": root_q.id, "text": "hi"})
        into_q_named_p = await code(
            client,
            "send_message",
            {"session_id": root_q.id, "text": "hi", "project_id": project_p},
        )
        into_q_alias_p = await code(
            client,
            "send_message",
            {"session_id": root_q.id, "text": "hi", "project": project_p},
        )
        # send_message into a child filed on P whose root is filed on Q
        into_child = await code(
            client, "send_message", {"session_id": child_filed_p_under_q.id, "text": "hi"}
        )
        # create_session under a Q-root parent
        under_q = await code(
            client,
            "create_session",
            {"agent_name": "missing-agent", "parent_session_id": root_q.id},
        )
        under_q_named_p = await code(
            client,
            "create_session",
            {
                "agent_name": "missing-agent",
                "parent_session_id": root_q.id,
                "project_id": project_p,
            },
        )
        # create_session under a P-root parent
        under_p_named_p = await code(
            client,
            "create_session",
            {
                "agent_name": "missing-agent",
                "parent_session_id": root_p.id,
                "project_id": project_p,
            },
        )
        under_p_named_q = await code(
            client,
            "create_session",
            {
                "agent_name": "missing-agent",
                "parent_session_id": root_p.id,
                "project_id": project_q,
            },
        )
        # send_message into a P-root session
        into_p = await code(client, "send_message", {"session_id": root_p.id, "text": "hi"})
        into_p_named_q = await code(
            client,
            "send_message",
            {"session_id": root_p.id, "text": "hi", "project_id": project_q},
        )
        # manage_session update moving a P-root session; runs last since it may refile
        move_p_into_q = await code(
            client,
            "manage_session",
            {
                "action": "update",
                "session_id": root_p.id,
                "project_id": project_q,
                "confirm": True,
            },
        )
        move_p_into_p = await code(
            client,
            "manage_session",
            {
                "action": "update",
                "session_id": root_p.id,
                "project_id": project_p,
                "confirm": True,
            },
        )

    gate_codes = {"onboarding_required", "suspended", "host_offline"}
    assert into_q == "onboarding_required"
    assert into_q_named_p == "onboarding_required"
    assert into_q_alias_p == "onboarding_required"
    assert into_child == "onboarding_required"
    assert under_q == "onboarding_required"
    assert under_q_named_p == "onboarding_required"
    assert under_p_named_p not in gate_codes
    assert under_p_named_q == "onboarding_required"
    assert into_p not in gate_codes
    assert into_p_named_q not in gate_codes
    assert move_p_into_q == "onboarding_required"
    assert move_p_into_p not in gate_codes
