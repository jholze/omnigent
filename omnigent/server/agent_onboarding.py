"""MCP agent onboarding: guide, answers, review, and owner approval.

Decisions for this version, where the spec left a question open:

* Guide files resolve from ``OMNIGENT_ONBOARDING_GUIDES_DIR/<project_id>.yaml``,
  then ``~/dev/omnigent-agents/<project_id>/onboarding.yaml``, then the bundled
  default. The hash is the file's SHA-256.
* ``onboarding_approve`` accepts a human owner or an admin. An ``ogc_`` token
  is forbidden. The orchestrator is a human owner or an agent whose handle is
  listed on the guide or in ``OMNIGENT_ONBOARDING_ORCHESTRATOR_HANDLES``.
* Until the target project's row is ``active``, writes for that project are
  refused. A call names the project via ``project_id``, a filed session, or
  the account's only project. Several projects and no target stay fail-closed.
  ``chat_*`` stays allowed. Findings come back from ``onboarding_status``.
* A bot with no onboarding row for that project is not gated, so existing
  accounts keep working until ``onboarding_start`` or ``onboarding_import``.
* Clocks advance when status is read. ``now_us`` is accepted only when
  ``OMNIGENT_ONBOARDING_ALLOW_CLOCK`` is true. There is no chat scheduler.
* Host offline keeps status ``invited`` and refuses ``create_session`` without
  ``host_id``. A missing host store reports ``unknown`` and does not block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from omnigent.db.utils import now_epoch_us
from omnigent.server.agent_onboarding_guide import (
    OnboardingGuide,
    guide_payload,
    load_guide,
)
from omnigent.server.agent_onboarding_secrets import contains_secret
from omnigent.server.chat_access import principal_kind
from omnigent.server.feature_flags import Feature
from omnigent.stores.onboarding_store import (
    AccountRef,
    OnboardingRecord,
    OnboardingStore,
)

_logger = logging.getLogger(__name__)

NOT_ONBOARDING = object()
_CLOCK_ENV = "OMNIGENT_ONBOARDING_ALLOW_CLOCK"
_HANDLES_ENV = "OMNIGENT_ONBOARDING_ORCHESTRATOR_HANDLES"
_MAX_REWORK = 3

TOOL_NAMES = frozenset(
    {
        "onboarding_get_guide",
        "onboarding_status",
        "onboarding_start",
        "onboarding_submit_answers",
        "onboarding_review",
        "onboarding_approve",
        "onboarding_suspend",
        "onboarding_reset",
        "onboarding_import",
    }
)
WRITE_TOOLS = TOOL_NAMES - {"onboarding_get_guide", "onboarding_status"}
CORE_WRITE_TOOLS = frozenset(
    {
        "create_session",
        "send_message",
        "interrupt_turn",
        "stop_session",
        "answer_elicitation",
    }
)
_WRITE_ACTIONS = frozenset(
    {
        "delete",
        "write",
        "edit",
        "shell",
        "set",
        "create",
        "update",
        "revoke",
        "remove",
        "add",
        "rename",
    }
)


class OnboardingError(Exception):
    """A refused onboarding call. ``code`` is safe to return to the client."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Caller:
    """The authenticated MCP principal."""

    principal_id: str
    kind: str
    is_admin: bool


class OnboardingService:
    """State machine over :class:`OnboardingStore`."""

    def __init__(
        self,
        storage_location: str,
        *,
        guides_dir: str | None = None,
        host_probe: Callable[[str], str] | None = None,
    ) -> None:
        self._store = OnboardingStore(storage_location)
        self._guides_dir = guides_dir
        self._host_probe = host_probe or (lambda _owner: "unknown")

    def get_guide(self, caller: Caller, project_id: str) -> dict[str, Any]:
        """Return the guide for a project the caller may see."""
        self._require_project_visible(caller, project_id)
        guide = self._guide(project_id)
        record = self._record_for_caller(caller, project_id)
        delta = record is not None and record.status == "refresh_required"
        payload = guide_payload(guide, project_id, delta=delta)
        if record is not None:
            payload["status"] = record.status
        return payload

    def status(
        self,
        caller: Caller,
        *,
        account_ref: str | None,
        project_id: str,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Read one record, advancing clocks and the access checks."""
        account = self._target_account(caller, account_ref)
        self._require_can_read(caller, account, project_id)
        now = self._now(now_us)
        guide = self._guide(project_id)
        record = self._store.get(account.id, project_id)
        if record is None:
            return self._absent(caller, account, project_id, guide, now)
        changed = self._refresh(record, account, guide, now)
        if changed:
            self._store.save(record)
        return self._view(caller, account, record, guide)

    def start(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        predecessor: str | None = None,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Open an invited record. A second call returns the same row."""
        account = self._require_account(account_ref, include_revoked=False)
        self._require_orchestrator(caller, account, project_id)
        if account.project_ids is None or project_id not in account.project_ids:
            raise OnboardingError("not_found")
        now = self._now(now_us)
        guide = self._guide(project_id)
        existing = self._store.get(account.id, project_id)
        if existing is not None:
            self._refresh(existing, account, guide, now)
            self._store.save(existing)
            view = self._view(caller, account, existing, guide)
            view["idempotent"] = True
            return view
        previous_id = self._predecessor_id(predecessor, project_id)
        record = OnboardingRecord(
            id=uuid.uuid4().hex,
            account_id=account.id,
            project_id=project_id,
            status="invited",
            guide_version=guide.version,
            guide_hash=guide.guide_hash,
            answers=None,
            answers_hash=None,
            rework_count=0,
            findings=[],
            owner_notices=[],
            bot_notices=[],
            review=None,
            archive=[],
            generation=1,
            previous_id=previous_id,
            opened_at=now,
            updated_at=now,
            submitted_at=None,
            approved_by=None,
            host_check=None,
        )
        saved = self._store.save(record)
        self._audit(caller, saved, "start", now, {"generation": saved.generation})
        _logger.info(
            "onboarding action=start account=%s project=%s status=%s",
            saved.account_id,
            saved.project_id,
            saved.status,
        )
        return self._view(caller, account, saved, guide)

    def submit(
        self,
        caller: Caller,
        *,
        project_id: str,
        guide_hash: str,
        answers: dict[str, str],
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Store answers for the caller's own account, or reject a secret."""
        account = self._require_subject(caller)
        if account.project_ids is None or project_id not in account.project_ids:
            raise OnboardingError("not_found")
        record = self._store.get(account.id, project_id)
        if record is None:
            raise OnboardingError("not_found")
        guide = self._guide(project_id)
        now = self._now(now_us)
        if guide_hash != guide.guide_hash:
            raise OnboardingError("guide_mismatch")
        if contains_secret(answers):
            self._reject_secret(caller, account, record, guide, now)
        questions = (
            guide.delta_questions if record.status == "refresh_required" else guide.questions
        )
        missing = [
            question.id
            for question in questions
            if question.required and not str(answers.get(question.id, "")).strip()
        ]
        if missing:
            raise OnboardingError("incomplete: " + ", ".join(missing))
        digest = _answers_hash(answers)
        if record.answers_hash == digest and record.status in {
            "submitted",
            "awaiting_owner",
            "active",
        }:
            view = self._view(caller, account, record, guide)
            view["idempotent"] = True
            return view
        if record.status not in {
            "invited",
            "access_ok",
            "rework",
            "rejected_auto",
            "refresh_required",
        }:
            raise OnboardingError("invalid_status")
        findings = _plausibility(guide, questions, answers)
        record.answers = dict(answers)
        record.answers_hash = digest
        record.findings = findings
        record.submitted_at = now
        record.updated_at = now
        record.guide_hash = guide.guide_hash
        record.guide_version = guide.version
        record.status = "awaiting_owner" if record.status == "refresh_required" else "submitted"
        saved = self._store.save(record)
        self._audit(
            caller,
            saved,
            "submit",
            now,
            {"answers_hash": digest, "finding_count": len(findings)},
        )
        return self._view(caller, account, saved, guide)

    def review(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        verdict: str,
        findings: list[str],
        reviewer: str | None,
        fallback_for_claude: bool,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Record PASS, REWORK, or ACCESS_FAILED. The third REWORK blocks."""
        account = self._require_account(account_ref, include_revoked=False)
        self._require_orchestrator(caller, account, project_id)
        if contains_secret(findings) or (reviewer is not None and contains_secret(reviewer)):
            raise OnboardingError("secret_detected")
        record = self._require_record(account.id, project_id)
        guide = self._guide(project_id)
        now = self._now(now_us)
        if verdict == "ACCESS_FAILED":
            return self._access_failed(caller, account, record, guide, now)
        if verdict == "PASS":
            return self._pass_review(
                caller, account, record, guide, now, reviewer, fallback_for_claude
            )
        if verdict != "REWORK":
            raise OnboardingError("invalid_status")
        if record.status != "submitted":
            raise OnboardingError("invalid_status")
        stored = [{"code": "rework", "message": text[:500]} for text in findings]
        record.findings = stored
        record.rework_count += 1
        record.updated_at = now
        if record.rework_count >= _MAX_REWORK:
            record.status = "blocked"
            _add_notice(
                record.owner_notices,
                "blocked",
                "review reached the rework limit",
                now,
            )
        else:
            record.status = "rework"
        _add_notice(
            record.bot_notices, f"rework_{record.rework_count}", "review requested changes", now
        )
        saved = self._store.save(record)
        self._audit(
            caller,
            saved,
            "review",
            now,
            {
                "verdict": "REWORK",
                "rework_count": saved.rework_count,
                "finding_count": len(stored),
            },
        )
        return self._view(caller, account, saved, guide)

    def approve(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Move ``awaiting_owner`` to ``active``. Agent tokens are forbidden."""
        account = self._require_account(account_ref, include_revoked=False)
        record = self._require_record(account.id, project_id)
        guide = self._guide(project_id)
        now = self._now(now_us)
        if caller.kind == "agent_account" or not _is_owner(caller, account):
            self._audit(caller, record, "approve_forbidden", now, {"code": "forbidden"})
            raise OnboardingError("forbidden")
        if record.status == "active":
            view = self._view(caller, account, record, guide)
            view["idempotent"] = True
            return view
        if record.status != "awaiting_owner":
            raise OnboardingError("invalid_status")
        record.status = "active"
        record.approved_by = caller.principal_id
        record.updated_at = now
        record.guide_hash = guide.guide_hash
        saved = self._store.save(record)
        self._audit(caller, saved, "approve", now, {})
        return self._view(caller, account, saved, guide)

    def suspend(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        reason: str,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Block the bot's write tools and leave an owner notice."""
        account = self._require_account(account_ref, include_revoked=False)
        self._require_orchestrator(caller, account, project_id)
        if contains_secret(reason):
            raise OnboardingError("secret_detected")
        record = self._require_record(account.id, project_id)
        guide = self._guide(project_id)
        now = self._now(now_us)
        if record.status == "suspended":
            view = self._view(caller, account, record, guide)
            view["idempotent"] = True
            return view
        record.status = "suspended"
        record.updated_at = now
        _add_notice(record.owner_notices, "suspended", reason[:200] or "suspended", now)
        saved = self._store.save(record)
        self._audit(caller, saved, "suspend", now, {"code": "suspended"})
        return self._view(caller, account, saved, guide)

    def reset(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Return the row to ``invited`` and archive the previous answers."""
        account = self._require_account(account_ref, include_revoked=False)
        self._require_orchestrator(caller, account, project_id)
        record = self._require_record(account.id, project_id)
        guide = self._guide(project_id)
        now = self._now(now_us)
        record.archive.append(
            {
                "generation": record.generation,
                "status": record.status,
                "answers_hash": record.answers_hash,
                "at": now,
            }
        )
        record.generation += 1
        record.status = "invited"
        record.answers = None
        record.answers_hash = None
        record.findings = []
        record.rework_count = 0
        record.submitted_at = None
        record.approved_by = None
        record.opened_at = now
        record.updated_at = now
        record.guide_hash = guide.guide_hash
        record.guide_version = guide.version
        saved = self._store.save(record)
        self._audit(caller, saved, "reset", now, {"generation": saved.generation})
        return self._view(caller, account, saved, guide)

    def import_existing(
        self,
        caller: Caller,
        *,
        account_ref: str,
        project_id: str,
        now_us: int | None = None,
    ) -> dict[str, Any]:
        """Mark an existing bot ``refresh_required`` so it confirms the delta."""
        account = self._require_account(account_ref, include_revoked=False)
        self._require_orchestrator(caller, account, project_id)
        if account.project_ids is None or project_id not in account.project_ids:
            raise OnboardingError("not_found")
        guide = self._guide(project_id)
        now = self._now(now_us)
        existing = self._store.get(account.id, project_id)
        if existing is not None:
            view = self._view(caller, account, existing, guide)
            view["idempotent"] = True
            return view
        record = OnboardingRecord(
            id=uuid.uuid4().hex,
            account_id=account.id,
            project_id=project_id,
            status="refresh_required",
            guide_version=guide.version,
            guide_hash=guide.guide_hash,
            answers=None,
            answers_hash=None,
            rework_count=0,
            findings=[],
            owner_notices=[],
            bot_notices=[],
            review={"imported": True},
            archive=[],
            generation=1,
            previous_id=None,
            opened_at=now,
            updated_at=now,
            submitted_at=None,
            approved_by=None,
            host_check=None,
        )
        _add_notice(record.bot_notices, "delta", "confirm the guide delta", now)
        saved = self._store.save(record)
        self._audit(caller, saved, "import", now, {})
        view = self._view(caller, account, saved, guide)
        exception = guide.merge_exceptions.get(account.handle)
        if exception is not None:
            view["merge_exception"] = exception
        return view

    def _reject_secret(
        self,
        caller: Caller,
        account: AccountRef,
        record: OnboardingRecord,
        guide: OnboardingGuide,
        now: int,
    ) -> None:
        record.status = "rejected_auto"
        record.answers = None
        record.answers_hash = None
        record.updated_at = now
        _add_notice(
            record.owner_notices,
            "secret_detected",
            "secret detected in submitted answers; rotation recommended",
            now,
        )
        saved = self._store.save(record)
        self._audit(caller, saved, "secret_rejected", now, {"code": "secret_detected"})
        _logger.info(
            "onboarding action=secret_rejected account=%s project=%s",
            saved.account_id,
            saved.project_id,
        )
        del account, guide
        raise OnboardingError("secret_detected")

    def _pass_review(
        self,
        caller: Caller,
        account: AccountRef,
        record: OnboardingRecord,
        guide: OnboardingGuide,
        now: int,
        reviewer: str | None,
        fallback_for_claude: bool,
    ) -> dict[str, Any]:
        if record.status == "awaiting_owner":
            view = self._view(caller, account, record, guide)
            view["idempotent"] = True
            return view
        if record.status != "submitted":
            raise OnboardingError("invalid_status")
        record.status = "awaiting_owner"
        record.updated_at = now
        follow_up = "opus_rereview" if fallback_for_claude else None
        record.review = {
            "verdict": "PASS",
            "reviewer": reviewer or "",
            "fallback_for_claude": fallback_for_claude,
            "follow_up": follow_up or "",
        }
        saved = self._store.save(record)
        self._audit(
            caller,
            saved,
            "review",
            now,
            {
                "verdict": "PASS",
                "fallback_for_claude": fallback_for_claude,
                "follow_up": follow_up or "",
            },
        )
        return self._view(caller, account, saved, guide)

    def _access_failed(
        self,
        caller: Caller,
        account: AccountRef,
        record: OnboardingRecord,
        guide: OnboardingGuide,
        now: int,
    ) -> dict[str, Any]:
        if record.status == "access_failed":
            view = self._view(caller, account, record, guide)
            view["idempotent"] = True
            return view
        record.status = "access_failed"
        record.updated_at = now
        _add_notice(record.owner_notices, "access_failed", "owner must rotate the token", now)
        saved = self._store.save(record)
        self._audit(caller, saved, "access_failed", now, {"code": "access_failed"})
        return self._view(caller, account, saved, guide)

    def _refresh(
        self,
        record: OnboardingRecord,
        account: AccountRef,
        guide: OnboardingGuide,
        now: int,
    ) -> bool:
        changed = False
        host = self._host_probe(account.owner_id)
        if host != record.host_check:
            record.host_check = host
            changed = True
        scope_ok = _exact_scope(account, record.project_id)
        if not scope_ok:
            before = len(record.owner_notices)
            _add_notice(
                record.owner_notices,
                "scope_mismatch",
                "account project scope is not exactly this project",
                now,
            )
            changed = changed or len(record.owner_notices) != before
        elif record.status == "invited" and host != "offline":
            record.status = "access_ok"
            changed = True
        if record.status == "active" and record.guide_hash != guide.guide_hash:
            record.status = "refresh_required"
            record.guide_hash = guide.guide_hash
            record.guide_version = guide.version
            _add_notice(record.bot_notices, "delta", "confirm the guide delta", now)
            _add_notice(record.owner_notices, "guide_changed", "guide hash changed", now)
            changed = True
        before_bot = len(record.bot_notices)
        before_owner = len(record.owner_notices)
        before_status = record.status
        _advance_clock(record, guide, now)
        if (
            record.status != before_status
            or len(record.bot_notices) != before_bot
            or len(record.owner_notices) != before_owner
        ):
            changed = True
        if changed:
            record.updated_at = now
        return changed

    def _absent(
        self,
        caller: Caller,
        account: AccountRef,
        project_id: str,
        guide: OnboardingGuide,
        now: int,
    ) -> dict[str, Any]:
        del now, guide
        payload = {
            "status": "absent",
            "account": account.handle,
            "project_id": project_id,
            "checks": _checks(account, project_id, self._host_probe(account.owner_id)),
            "token_prefix_ok": True,
        }
        if _can_see_owner(caller, account):
            payload["owner_notices"] = []
        return payload

    def _view(
        self,
        caller: Caller,
        account: AccountRef,
        record: OnboardingRecord,
        guide: OnboardingGuide,
    ) -> dict[str, Any]:
        host = record.host_check or self._host_probe(account.owner_id)
        payload: dict[str, Any] = {
            "status": record.status,
            "account": account.handle,
            "project_id": record.project_id,
            "guide_version": record.guide_version,
            "guide_hash": record.guide_hash,
            "rework_count": record.rework_count,
            "findings": record.findings,
            "bot_notices": record.bot_notices,
            "checks": _checks(account, record.project_id, host),
            "token_prefix_ok": True,
            "answers_stored": record.answers_hash is not None,
            "generation": record.generation,
            "previous_id": record.previous_id,
            "host": host,
        }
        if record.status == "refresh_required":
            payload["delta"] = True
        if record.status == "awaiting_owner":
            payload["review_passed"] = True
        exception = guide.merge_exceptions.get(account.handle)
        if exception is not None:
            payload["merge_exception"] = exception
        if _can_see_owner(caller, account):
            payload["owner_notices"] = record.owner_notices
            payload["approved_by"] = record.approved_by
            payload["audit"] = [
                {
                    "action": entry.action,
                    "at": entry.at,
                    "actor_kind": entry.actor_kind,
                    "status_after": entry.status_after,
                    "detail": entry.detail,
                }
                for entry in self._store.audits(record.id)
            ]
            payload["archive"] = [
                {
                    "generation": item.get("generation"),
                    "status": item.get("status"),
                    "answers_hash": item.get("answers_hash"),
                }
                for item in record.archive
            ]
        return payload

    def _guide(self, project_id: str) -> OnboardingGuide:
        return load_guide(project_id, guides_dir=self._guides_dir)

    def _now(self, now_us: int | None) -> int:
        if now_us is None:
            return now_epoch_us()
        if not clock_allowed():
            raise OnboardingError("forbidden")
        return now_us

    def _require_record(self, account_id: str, project_id: str) -> OnboardingRecord:
        record = self._store.get(account_id, project_id)
        if record is None:
            raise OnboardingError("not_found")
        return record

    def _require_account(self, ref: str, *, include_revoked: bool) -> AccountRef:
        account = self._store.account_by_ref(ref, include_revoked=include_revoked)
        if account is None or account.revoked:
            raise OnboardingError("not_found")
        return account

    def _require_subject(self, caller: Caller) -> AccountRef:
        if caller.kind != "agent_account":
            raise OnboardingError("forbidden")
        account = self._store.account_by_principal(caller.principal_id)
        if account is None:
            raise OnboardingError("not_found")
        return account

    def _target_account(self, caller: Caller, account_ref: str | None) -> AccountRef:
        if account_ref:
            return self._require_account(account_ref, include_revoked=False)
        return self._require_subject(caller)

    def _require_can_read(self, caller: Caller, account: AccountRef, project_id: str) -> None:
        if caller.kind == "agent_account" and caller.principal_id == account.principal_id:
            if account.project_ids is not None and project_id not in account.project_ids:
                raise OnboardingError("not_found")
            return
        self._require_orchestrator(caller, account, project_id)

    def _require_project_visible(self, caller: Caller, project_id: str) -> None:
        if caller.kind == "agent_account":
            account = self._store.account_by_principal(caller.principal_id)
            if account is None or account.project_ids is None:
                raise OnboardingError("not_found")
            if project_id in account.project_ids:
                return
            raise OnboardingError("not_found")
        if caller.is_admin or self._store.project_owned(caller.principal_id, project_id):
            return
        raise OnboardingError("not_found")

    def _require_orchestrator(self, caller: Caller, account: AccountRef, project_id: str) -> None:
        if _is_owner(caller, account):
            return
        if caller.kind != "agent_account":
            raise OnboardingError("not_found")
        agent = self._store.account_by_principal(caller.principal_id)
        if agent is None or agent.id == account.id:
            raise OnboardingError("not_found")
        if agent.project_ids is None or project_id not in agent.project_ids:
            raise OnboardingError("not_found")
        handles = set(self._guide(project_id).orchestrator_handles) | extra_orchestrator_handles()
        if agent.handle not in handles:
            raise OnboardingError("forbidden")

    def _record_for_caller(self, caller: Caller, project_id: str) -> OnboardingRecord | None:
        if caller.kind != "agent_account":
            return None
        account = self._store.account_by_principal(caller.principal_id)
        if account is None:
            return None
        return self._store.get(account.id, project_id)

    def _predecessor_id(self, predecessor: str | None, project_id: str) -> str | None:
        if not predecessor:
            return None
        previous = self._store.account_by_ref(predecessor, include_revoked=True)
        if previous is None:
            return None
        row = self._store.get(previous.id, project_id)
        return row.id if row is not None else None

    def _audit(
        self,
        caller: Caller,
        record: OnboardingRecord,
        action: str,
        now: int,
        detail: dict[str, object],
    ) -> None:
        self._store.add_audit(
            record,
            at=now,
            actor_kind=caller.kind,
            actor_id=caller.principal_id,
            action=action,
            detail=detail,
        )


def clock_allowed() -> bool:
    """Whether callers may pass ``now_us`` to move the onboarding clock."""
    return os.environ.get(_CLOCK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def extra_orchestrator_handles() -> set[str]:
    """Handles from the environment that may review, in addition to the guide."""
    raw = os.environ.get(_HANDLES_ENV, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def onboarding_enabled(request: Any) -> bool:
    """Whether this process enabled the agent_onboarding feature."""
    flags = getattr(getattr(request, "app", None), "state", None)
    feature_flags = getattr(flags, "feature_flags", None)
    enabled = getattr(feature_flags, "enabled", None)
    if enabled is None:
        return False
    return bool(enabled(Feature.AGENT_ONBOARDING))


def onboarding_tool_definitions() -> list[dict[str, Any]]:
    """Tool descriptors added to ``tools/list`` when onboarding is enabled."""
    confirm = {
        "type": "boolean",
        "description": "Perform the change. Default false returns a dry run and writes nothing.",
    }
    account = {"type": "string", "description": "Agent account handle or id."}
    project = {"type": "string", "description": "Project id in the account scope."}
    now = {
        "type": "integer",
        "description": "Epoch microseconds. Accepted only on a test server.",
    }
    return [
        _tool(
            "onboarding_get_guide",
            "Read the onboarding guide for a project in the caller scope. "
            "Includes version and guide_hash. Other projects are not_found.",
            {"project_id": project},
            ["project_id"],
        ),
        _tool(
            "onboarding_status",
            "Read onboarding status, access checks, findings, and owner notices. "
            "Owner notices are omitted for the bot. Nothing is posted to chat.",
            {"account": account, "project_id": project, "now_us": now},
            ["project_id"],
        ),
        _tool(
            "onboarding_start",
            "Open onboarding for an account and project. Idempotent. Orchestrator or owner. "
            "Dry-run unless confirm is true.",
            {
                "account": account,
                "project_id": project,
                "predecessor_account": {"type": "string"},
                "now_us": now,
                "confirm": confirm,
            },
            ["account", "project_id"],
        ),
        _tool(
            "onboarding_submit_answers",
            "Submit questionnaire answers for the caller's own account. "
            "A secret is rejected and not stored. Dry-run unless confirm is true.",
            {
                "project_id": project,
                "guide_hash": {"type": "string"},
                "answers": {"type": "object", "additionalProperties": {"type": "string"}},
                "now_us": now,
                "confirm": confirm,
            },
            ["project_id", "guide_hash", "answers"],
        ),
        _tool(
            "onboarding_review",
            "Orchestrator verdict PASS, REWORK, or ACCESS_FAILED. "
            "The third REWORK sets blocked. Dry-run unless confirm is true.",
            {
                "account": account,
                "project_id": project,
                "verdict": {"type": "string", "enum": ["PASS", "REWORK", "ACCESS_FAILED"]},
                "findings": {"type": "array", "items": {"type": "string"}},
                "reviewer": {"type": "string"},
                "fallback_for_claude": {"type": "boolean"},
                "now_us": now,
                "confirm": confirm,
            },
            ["account", "project_id", "verdict"],
        ),
        _tool(
            "onboarding_approve",
            "Owner approval from awaiting_owner to active. An agent token is forbidden. "
            "Dry-run unless confirm is true.",
            {"account": account, "project_id": project, "now_us": now, "confirm": confirm},
            ["account", "project_id"],
        ),
        _tool(
            "onboarding_suspend",
            "Set suspended and tell the owner through onboarding_status. "
            "Write tools then return suspended. Dry-run unless confirm is true.",
            {
                "account": account,
                "project_id": project,
                "reason": {"type": "string"},
                "now_us": now,
                "confirm": confirm,
            },
            ["account", "project_id", "reason"],
        ),
        _tool(
            "onboarding_reset",
            "Archive the current answers and return to invited. Dry-run unless confirm is true.",
            {"account": account, "project_id": project, "now_us": now, "confirm": confirm},
            ["account", "project_id"],
        ),
        _tool(
            "onboarding_import",
            "Create a refresh_required row for a bot that has no onboarding record. "
            "Dry-run unless confirm is true.",
            {
                "account": account,
                "project_id": project,
                "existing": {"type": "boolean"},
                "now_us": now,
                "confirm": confirm,
            },
            ["account", "project_id"],
        ),
    ]


def _in_account_scope(account: AccountRef, project_id: str) -> bool:
    if account.project_ids is None:
        return True
    return project_id in account.project_ids


def _resolve_gate_project(
    account: AccountRef,
    args: Mapping[str, Any],
    rows: tuple[OnboardingRecord, ...],
    session_project_id: str | None,
) -> tuple[str | None, bool]:
    """Return ``(project_id, specific)`` for the write gate.

    ``specific`` is true when the write belongs to one project. An empty
    row match for that project is not gated. ``specific`` false keeps every
    row, so a foreign id or an unscoped multi-project write stays fail-closed.
    """
    named = _text(args.get("project_id"))
    if named is not None:
        if _in_account_scope(account, named):
            return named, True
        return None, False
    alias = _text(args.get("project"))
    if alias is not None and any(row.project_id == alias for row in rows):
        if _in_account_scope(account, alias):
            return alias, True
        return None, False
    if session_project_id and _in_account_scope(account, session_project_id):
        return session_project_id, True
    ids = account.project_ids
    if ids is not None and len(set(ids)) == 1:
        return ids[0], True
    return None, False


def _session_project_id(store: OnboardingStore, args: Mapping[str, Any]) -> str | None:
    session_id = _text(args.get("session_id"))
    if session_id is None:
        return None
    try:
        return store.session_project_id(session_id)
    except Exception:  # noqa: BLE001 — a lookup failure stays fail-closed
        _logger.info("onboarding gate could not read the session project")
        return None


def reject_blocked_write(
    request: Any,
    auth_provider: Any,
    name: str,
    args: Mapping[str, Any],
) -> None:
    """Refuse a write while the target project's onboarding row is not active.

    The target is ``project_id``, else the session's project, else the
    account's only project. Several projects and no target keep every row.
    Chat and onboarding tools are not gated. No row means not gated.
    """
    from omnigent.server.mcp_endpoint import ToolFailure

    if not onboarding_enabled(request):
        return
    if name.startswith(("onboarding_", "chat_")):
        return
    user_id = _caller_principal(request, auth_provider)
    if user_id is None or principal_kind(user_id) != "agent_account":
        return
    store = _store_for_request(request)
    if store is None:
        return
    account = store.account_by_principal(user_id)
    if account is None:
        return
    rows = store.list_for_account(account.id)
    if not rows:
        return
    session_project = None
    if _text(args.get("project_id")) is None:
        session_project = _session_project_id(store, args)
    target, specific = _resolve_gate_project(account, args, rows, session_project)
    relevant = rows
    if specific and target is not None:
        relevant = tuple(row for row in rows if row.project_id == target)
        if not relevant:
            return
    if name == "create_session" and not _text(args.get("host_id")):
        if any(row.host_check == "offline" for row in relevant):
            raise ToolFailure("host offline", code="host_offline")
    if not _write_attempt(name, args):
        return
    if any(row.status == "suspended" for row in relevant):
        raise ToolFailure("suspended", code="suspended")
    if any(row.status != "active" for row in relevant):
        raise ToolFailure("onboarding_required", code="onboarding_required")


async def call_onboarding_tool(
    request: Any,
    auth_provider: Any,
    name: str,
    args: dict[str, Any],
) -> Any:
    """Run one onboarding tool, or :data:`NOT_ONBOARDING` when ``name`` is not one."""
    from omnigent.server.mcp_endpoint import ToolFailure, _preview

    if name not in TOOL_NAMES:
        return NOT_ONBOARDING
    if not onboarding_enabled(request):
        raise ToolFailure("not_found", code="not_found")
    if name in WRITE_TOOLS and args.get("confirm") is not True:
        body = {
            key: value
            for key, value in args.items()
            if key not in {"answers", "findings", "reason", "confirm"}
        }
        return _preview("ONBOARDING", f"/mcp/{name}", body)
    try:
        return _dispatch(request, auth_provider, name, args)
    except OnboardingError as exc:
        _logger.info("onboarding tool=%s code=%s", name, exc.code)
        raise ToolFailure(str(exc), code=exc.code) from None


def read_onboarding_resource(request: Any, auth_provider: Any, uri: str) -> dict[str, Any]:
    """Read ``omnigent://onboarding/<project_id>`` as the guide JSON."""
    prefix = "omnigent://onboarding/"
    if not uri.startswith(prefix) or uri == prefix:
        raise OnboardingError("not_found")
    project_id = uri[len(prefix) :]
    if any(char in project_id for char in " /?#\\"):
        raise OnboardingError("not_found")
    payload = _service(request).get_guide(_caller(request, auth_provider), project_id)
    text = json.dumps(payload, sort_keys=True)
    return {
        "contents": [
            {"uri": uri, "mimeType": "application/json", "text": text},
        ]
    }


def list_onboarding_resources() -> list[dict[str, str]]:
    """The single guide resource template advertised by ``resources/list``."""
    return [
        {
            "uri": "omnigent://onboarding/guide",
            "name": "agent-onboarding-guide",
            "mimeType": "application/json",
            "description": "Read omnigent://onboarding/<project_id> for one project in scope.",
        }
    ]


def _dispatch(request: Any, auth_provider: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    service = _service(request)
    caller = _caller(request, auth_provider)
    project_id = _required_str(args, "project_id")
    now_us = _optional_int(args.get("now_us"))
    if name == "onboarding_get_guide":
        return service.get_guide(caller, project_id)
    if name == "onboarding_status":
        return service.status(
            caller,
            account_ref=_optional_str(args.get("account")),
            project_id=project_id,
            now_us=now_us,
        )
    if name == "onboarding_start":
        return service.start(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            predecessor=_optional_str(args.get("predecessor_account")),
            now_us=now_us,
        )
    if name == "onboarding_submit_answers":
        return service.submit(
            caller,
            project_id=project_id,
            guide_hash=_required_str(args, "guide_hash"),
            answers=_string_map(args.get("answers")),
            now_us=now_us,
        )
    if name == "onboarding_review":
        return service.review(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            verdict=_required_str(args, "verdict"),
            findings=_string_list(args.get("findings")),
            reviewer=_optional_str(args.get("reviewer")),
            fallback_for_claude=bool(args.get("fallback_for_claude") is True),
            now_us=now_us,
        )
    if name == "onboarding_approve":
        return service.approve(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            now_us=now_us,
        )
    if name == "onboarding_suspend":
        return service.suspend(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            reason=_required_str(args, "reason"),
            now_us=now_us,
        )
    if name == "onboarding_reset":
        return service.reset(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            now_us=now_us,
        )
    if name == "onboarding_import":
        return service.import_existing(
            caller,
            account_ref=_required_str(args, "account"),
            project_id=project_id,
            now_us=now_us,
        )
    raise OnboardingError("not_found")


def _service(request: Any) -> OnboardingService:
    store = _store_for_request(request)
    if store is None:
        raise OnboardingError("not_found")

    def probe(owner_id: str) -> str:
        host_store = getattr(request.app.state, "host_store", None)
        if host_store is None:
            return "unknown"
        try:
            from omnigent.stores.host_store import host_is_live

            hosts = host_store.list_hosts(owner_id)
        except Exception:  # noqa: BLE001 — a probe failure is offline, not a crash
            _logger.info("onboarding host probe failed")
            return "offline"
        if any(host_is_live(host) for host in hosts):
            return "online"
        return "offline"

    return OnboardingService(store.storage_location, host_probe=probe)


def _store_for_request(request: Any) -> OnboardingStore | None:
    chat = getattr(getattr(request.app, "state", None), "team_chat_store", None)
    location = getattr(chat, "storage_location", None)
    if not isinstance(location, str) or not location:
        return None
    return OnboardingStore(location)


def _caller(request: Any, auth_provider: Any) -> Caller:
    from omnigent.server.routes._auth_helpers import require_user

    principal = require_user(request, auth_provider)
    if not isinstance(principal, str) or not principal:
        raise OnboardingError("forbidden")
    kind = principal_kind(principal)
    is_admin = False
    if kind == "user":
        permission_store = getattr(request.app.state, "permission_store", None)
        checker = getattr(permission_store, "is_admin", None)
        if checker is not None:
            try:
                is_admin = bool(checker(principal))
            except Exception:  # noqa: BLE001 — admin lookup must not widen access
                _logger.info("onboarding admin check failed")
                is_admin = False
    return Caller(principal_id=principal, kind=kind, is_admin=is_admin)


def _caller_principal(request: Any, auth_provider: Any) -> str | None:
    from omnigent.server.routes._auth_helpers import require_user

    provider = auth_provider
    if provider is None:
        provider = getattr(getattr(request.app, "state", None), "mcp_registry_auth", None)
    try:
        principal = require_user(request, provider)
    except Exception:  # noqa: BLE001 — an auth failure is an anonymous caller
        return None
    return principal if isinstance(principal, str) else None


def _exact_scope(account: AccountRef, project_id: str) -> bool:
    if account.project_ids is None:
        return False
    return set(account.project_ids) == {project_id}


def _is_owner(caller: Caller, account: AccountRef) -> bool:
    return caller.kind == "user" and (caller.is_admin or caller.principal_id == account.owner_id)


def _can_see_owner(caller: Caller, account: AccountRef) -> bool:
    if _is_owner(caller, account):
        return True
    return caller.kind == "agent_account" and caller.principal_id != account.principal_id


def _checks(account: AccountRef, project_id: str, host: str) -> list[dict[str, str]]:
    scope = "ok" if _exact_scope(account, project_id) else "scope_mismatch"
    host_result = host if host in {"online", "offline", "unknown"} else "unknown"
    hint = ""
    if host_result == "offline":
        hint = "host is offline; do not create a session without host_id"
    return [
        {"name": "token", "result": "ok"},
        {"name": "scope", "result": scope},
        {"name": "host", "result": host_result, "hint": hint},
    ]


def _advance_clock(record: OnboardingRecord, guide: OnboardingGuide, now: int) -> None:
    if record.status in {"invited", "access_ok"} and record.submitted_at is None:
        age = now - record.opened_at
        if age >= guide.stall_us:
            record.status = "stalled"
            _add_notice(
                record.owner_notices, "stalled", "no answers before the stall threshold", now
            )
            _add_notice(record.bot_notices, "stalled", "onboarding stalled before submit", now)
        elif age >= guide.remind_2_us:
            _add_notice(record.bot_notices, "remind_48", "submit is still missing", now)
        elif age >= guide.remind_1_us:
            _add_notice(record.bot_notices, "remind_24", "submit is still missing", now)
    if record.status != "awaiting_owner":
        return
    last_digest = _notice_at(record.owner_notices, "owner_digest")
    anchor = last_digest if last_digest is not None else record.updated_at
    if now - anchor >= guide.owner_digest_us:
        record.owner_notices.append(
            {"code": "owner_digest", "message": "approval is still waiting", "at": now}
        )
    if now - record.updated_at >= guide.owner_wait_us:
        _add_notice(
            record.owner_notices,
            "owner_wait",
            "approval has waited past the owner threshold",
            now,
        )


def _add_notice(notices: list[dict[str, object]], code: str, message: str, now: int) -> None:
    if any(item.get("code") == code for item in notices):
        return
    notices.append({"code": code, "message": message, "at": now})


def _notice_at(notices: list[dict[str, object]], code: str) -> int | None:
    found = [item.get("at") for item in notices if item.get("code") == code]
    last = found[-1] if found else None
    return last if isinstance(last, int) else None


def _plausibility(
    guide: OnboardingGuide,
    questions: tuple[Any, ...],
    answers: Mapping[str, str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for question in questions:
        answer = answers.get(question.id, "")
        expected = None
        if question.matches == "integration_branch":
            expected = guide.integration_branch
        elif question.matches_const is not None:
            expected = question.matches_const
        if expected is not None and answer.strip() != expected:
            findings.append(
                {
                    "code": "plausibility",
                    "field": question.id,
                    "message": f"{question.id} does not match the guide",
                }
            )
    return findings


def _answers_hash(answers: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(answers), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_attempt(name: str, args: Mapping[str, Any]) -> bool:
    if name in CORE_WRITE_TOOLS:
        return True
    if args.get("confirm") is True:
        return True
    action = args.get("action")
    return isinstance(action, str) and action in _WRITE_ACTIONS


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _required_str(args: Mapping[str, Any], name: str) -> str:
    value = _text(args.get(name))
    if value is None:
        raise OnboardingError("incomplete: " + name)
    return value


def _optional_str(value: object) -> str | None:
    return _text(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise OnboardingError("incomplete: now_us")
    return value


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise OnboardingError("incomplete: answers")
    mapped: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise OnboardingError("incomplete: answers")
        mapped[key] = item
    return mapped


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise OnboardingError("incomplete: findings")
    return list(value)


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}
