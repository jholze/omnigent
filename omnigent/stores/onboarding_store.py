"""SQL store for agent onboarding records and the audit log."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlAgentOnboarding,
    SqlAgentOnboardingAudit,
    SqlChatAgentAccount,
    SqlConversationMetadata,
    SqlProject,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)
from omnigent.stores.chat_store.sqlalchemy_store import decode_project_ids


@dataclass
class AccountRef:
    """Agent account fields the onboarding flow is allowed to see.

    The token hash and the raw token are not copied.
    """

    id: str
    handle: str
    owner_id: str
    principal_id: str
    project_ids: tuple[str, ...] | None
    revoked: bool


@dataclass
class OnboardingRecord:
    """Detached onboarding row."""

    id: str
    account_id: str
    project_id: str
    status: str
    guide_version: str
    guide_hash: str
    answers: dict[str, str] | None
    answers_hash: str | None
    rework_count: int
    findings: list[dict[str, str]]
    owner_notices: list[dict[str, object]]
    bot_notices: list[dict[str, object]]
    review: dict[str, object] | None
    archive: list[dict[str, object]]
    generation: int
    previous_id: str | None
    opened_at: int
    updated_at: int
    submitted_at: int | None
    approved_by: str | None
    host_check: str | None


@dataclass(frozen=True)
class AuditEntry:
    """One audit row. ``detail`` is codes and counts."""

    id: str
    onboarding_id: str
    at: int
    actor_kind: str
    actor_id: str
    action: str
    status_after: str
    guide_hash: str
    detail: dict[str, object]


class OnboardingStore:
    """Persist onboarding rows in the Omnigent database."""

    def __init__(self, storage_location: str) -> None:
        self.storage_location = storage_location
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.onboarding_store"
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.onboarding_store",
            immediate=True,
        )

    def get(self, account_id: str, project_id: str) -> OnboardingRecord | None:
        """Return the live row for this account and project, if any."""
        with self._session("get_agent_onboarding") as session:
            row = _one(session, account_id, project_id)
            return _to_record(row) if row is not None else None

    def list_for_account(self, account_id: str) -> tuple[OnboardingRecord, ...]:
        """Return every onboarding row for *account_id* in this workspace."""
        with self._session("list_agent_onboarding") as session:
            rows = session.execute(
                select(SqlAgentOnboarding).where(
                    SqlAgentOnboarding.workspace_id == current_workspace_id(),
                    SqlAgentOnboarding.account_id == account_id,
                )
            ).scalars()
            return tuple(_to_record(row) for row in rows)

    def save(self, record: OnboardingRecord) -> OnboardingRecord:
        """Insert or update *record*. A duplicate account/project returns the existing row."""

        def _write(session: Session) -> OnboardingRecord:
            row = _one(session, record.account_id, record.project_id)
            if row is None:
                row = SqlAgentOnboarding(
                    id=record.id or uuid.uuid4().hex,
                    account_id=record.account_id,
                    project_id=record.project_id,
                    status=record.status,
                    guide_version=record.guide_version,
                    guide_hash=record.guide_hash,
                    opened_at=record.opened_at,
                    updated_at=record.updated_at,
                    findings_json="[]",
                    owner_notices_json="[]",
                    bot_notices_json="[]",
                    archive_json="[]",
                    rework_count=0,
                    generation=1,
                )
                session.add(row)
            _apply(row, record)
            session.flush()
            return _to_record(row)

        try:
            return run_write_transaction(self._session_immediate, "save_agent_onboarding", _write)
        except IntegrityError:
            existing = self.get(record.account_id, record.project_id)
            if existing is None:
                raise
            return existing

    def add_audit(
        self,
        record: OnboardingRecord,
        *,
        at: int,
        actor_kind: str,
        actor_id: str,
        action: str,
        detail: dict[str, object],
    ) -> None:
        """Append one audit row. *detail* must not contain answer text or secrets."""

        def _write(session: Session) -> None:
            session.add(
                SqlAgentOnboardingAudit(
                    id=uuid.uuid4().hex,
                    onboarding_id=record.id,
                    at=at,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    action=action,
                    status_after=record.status,
                    guide_hash=record.guide_hash,
                    detail_json=json.dumps(detail, separators=(",", ":"), sort_keys=True),
                )
            )

        run_write_transaction(self._session_immediate, "audit_agent_onboarding", _write)

    def audits(self, onboarding_id: str) -> tuple[AuditEntry, ...]:
        """Return audit rows for one onboarding id, oldest first."""
        with self._session("list_agent_onboarding_audit") as session:
            rows = session.execute(
                select(SqlAgentOnboardingAudit)
                .where(
                    SqlAgentOnboardingAudit.workspace_id == current_workspace_id(),
                    SqlAgentOnboardingAudit.onboarding_id == onboarding_id,
                )
                .order_by(SqlAgentOnboardingAudit.at, SqlAgentOnboardingAudit.id)
            ).scalars()
            return tuple(_to_audit(row) for row in rows)

    def account_by_ref(self, ref: str, *, include_revoked: bool = False) -> AccountRef | None:
        """Resolve a handle or account id. The token hash is not loaded into the result."""
        with self._session("get_onboarding_account") as session:
            statement = select(SqlChatAgentAccount).where(
                SqlChatAgentAccount.workspace_id == current_workspace_id()
            )
            if not include_revoked:
                statement = statement.where(SqlChatAgentAccount.revoked_at.is_(None))
            rows = session.execute(statement).scalars()
            for row in rows:
                if row.handle == ref or row.id == ref:
                    return _to_account(row)
        return None

    def session_project_id(self, session_id: str) -> str | None:
        """Return the project a session is filed in, if it has one."""
        with self._session("onboarding_session_project") as session:
            row = session.execute(
                select(SqlConversationMetadata.project_id).where(
                    SqlConversationMetadata.workspace_id == current_workspace_id(),
                    SqlConversationMetadata.id == session_id,
                )
            ).first()
        if row is None:
            return None
        project_id = row[0]
        if isinstance(project_id, str) and project_id:
            return project_id
        return None

    def project_owned(self, user_id: str, project_id: str) -> bool:
        """Whether *user_id* owns *project_id* in this workspace."""
        with self._session("onboarding_project_owned") as session:
            row = session.execute(
                select(SqlProject.id).where(
                    SqlProject.workspace_id == current_workspace_id(),
                    SqlProject.id == project_id,
                    SqlProject.user_id == user_id,
                )
            ).first()
            return row is not None

    def account_by_principal(self, principal_id: str) -> AccountRef | None:
        """Resolve a live ``agent:`` principal."""
        with self._session("get_onboarding_account_principal") as session:
            row = session.execute(
                select(SqlChatAgentAccount).where(
                    SqlChatAgentAccount.workspace_id == current_workspace_id(),
                    SqlChatAgentAccount.principal_id == principal_id,
                    SqlChatAgentAccount.revoked_at.is_(None),
                )
            ).scalar_one_or_none()
            return _to_account(row) if row is not None else None


def _one(session: Session, account_id: str, project_id: str) -> SqlAgentOnboarding | None:
    return session.execute(
        select(SqlAgentOnboarding).where(
            SqlAgentOnboarding.workspace_id == current_workspace_id(),
            SqlAgentOnboarding.account_id == account_id,
            SqlAgentOnboarding.project_id == project_id,
        )
    ).scalar_one_or_none()


def _apply(row: SqlAgentOnboarding, record: OnboardingRecord) -> None:
    row.status = record.status
    row.guide_version = record.guide_version
    row.guide_hash = record.guide_hash
    row.answers_json = (
        json.dumps(record.answers, separators=(",", ":"), sort_keys=True)
        if record.answers
        else None
    )
    row.answers_hash = record.answers_hash
    row.rework_count = record.rework_count
    row.findings_json = _dump(record.findings)
    row.owner_notices_json = _dump(record.owner_notices)
    row.bot_notices_json = _dump(record.bot_notices)
    row.review_json = _dump(record.review) if record.review is not None else None
    row.archive_json = _dump(record.archive)
    row.generation = record.generation
    row.previous_id = record.previous_id
    row.opened_at = record.opened_at
    row.updated_at = record.updated_at
    row.submitted_at = record.submitted_at
    row.approved_by = record.approved_by
    row.host_check = record.host_check


def _to_record(row: SqlAgentOnboarding) -> OnboardingRecord:
    answers = _load_dict(row.answers_json)
    return OnboardingRecord(
        id=row.id,
        account_id=row.account_id,
        project_id=row.project_id,
        status=row.status,
        guide_version=row.guide_version,
        guide_hash=row.guide_hash,
        answers={key: value for key, value in answers.items() if isinstance(value, str)}
        if answers
        else None,
        answers_hash=row.answers_hash,
        rework_count=row.rework_count,
        findings=_load_objects(row.findings_json),
        owner_notices=_load_notices(row.owner_notices_json),
        bot_notices=_load_notices(row.bot_notices_json),
        review=_load_notice(row.review_json),
        archive=_load_notices(row.archive_json),
        generation=row.generation,
        previous_id=row.previous_id,
        opened_at=row.opened_at,
        updated_at=row.updated_at,
        submitted_at=row.submitted_at,
        approved_by=row.approved_by,
        host_check=row.host_check,
    )


def _to_audit(row: SqlAgentOnboardingAudit) -> AuditEntry:
    detail = _load_notice(row.detail_json) or {}
    return AuditEntry(
        id=row.id,
        onboarding_id=row.onboarding_id,
        at=row.at,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        action=row.action,
        status_after=row.status_after,
        guide_hash=row.guide_hash,
        detail=detail,
    )


def _to_account(row: SqlChatAgentAccount) -> AccountRef:
    return AccountRef(
        id=row.id,
        handle=row.handle,
        owner_id=row.owner_id,
        principal_id=row.principal_id,
        project_ids=decode_project_ids(row.project_ids),
        revoked=row.revoked_at is not None,
    )


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _load_dict(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _load_objects(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    objects: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            objects.append(
                {
                    key: text
                    for key, text in item.items()
                    if isinstance(key, str) and isinstance(text, str)
                }
            )
    return objects


def _load_notices(raw: str | None) -> list[dict[str, object]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _load_notice(raw: str | None) -> dict[str, object] | None:
    return _load_dict(raw)
