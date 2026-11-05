"""Load a versioned onboarding guide and hash its bytes.

Lookup order:

1. ``OMNIGENT_ONBOARDING_GUIDES_DIR/<project_id>.yaml`` when that directory is set.
2. ``~/dev/omnigent-agents/<project_id>/onboarding.yaml`` when that file exists.
3. The bundled default guide.

The hash is the SHA-256 of the file bytes, so an edit moves active bots to
``refresh_required``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_BUNDLED = Path(__file__).resolve().parents[1] / "resources" / "onboarding" / "default.yaml"
_GUIDES_ENV = "OMNIGENT_ONBOARDING_GUIDES_DIR"


@dataclass(frozen=True)
class GuideQuestion:
    """One required or optional questionnaire field."""

    id: str
    prompt: str
    required: bool
    matches: str | None
    matches_const: str | None


@dataclass(frozen=True)
class OnboardingGuide:
    """A parsed guide plus the hash of the file that produced it."""

    version: str
    guide_hash: str
    integration_branch: str
    workspace: str | None
    repo: str | None
    host_id: str | None
    orchestrator_handles: tuple[str, ...]
    merge_exceptions: dict[str, str]
    rules: tuple[str, ...]
    questions: tuple[GuideQuestion, ...]
    delta_questions: tuple[GuideQuestion, ...]
    remind_1_us: int
    remind_2_us: int
    stall_us: int
    owner_digest_us: int
    owner_wait_us: int
    client_retry_s: tuple[int, ...]
    stall_after_s: int
    source: str


def load_guide(project_id: str, *, guides_dir: str | None = None) -> OnboardingGuide:
    """Load the guide for *project_id*.

    *guides_dir* overrides ``OMNIGENT_ONBOARDING_GUIDES_DIR`` for tests.
    """
    path = _resolve_path(project_id, guides_dir)
    raw_bytes = path.read_bytes()
    loaded = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"onboarding guide {path.name} is not a mapping")
    timeouts = loaded.get("timeouts") if isinstance(loaded.get("timeouts"), dict) else {}
    retries = loaded.get("client_retry_s")
    retry_values = (
        tuple(int(item) for item in retries) if isinstance(retries, list) else (10, 60, 300)
    )
    return OnboardingGuide(
        version=str(loaded.get("version") or "0"),
        guide_hash=hashlib.sha256(raw_bytes).hexdigest(),
        integration_branch=str(loaded.get("integration_branch") or ""),
        workspace=_optional_str(loaded.get("workspace")),
        repo=_optional_str(loaded.get("repo")),
        host_id=_optional_str(loaded.get("host_id")),
        orchestrator_handles=_str_tuple(loaded.get("orchestrator_handles")),
        merge_exceptions=_str_map(loaded.get("merge_exceptions")),
        rules=_str_tuple(loaded.get("rules")),
        questions=_questions(loaded.get("questions")),
        delta_questions=_questions(loaded.get("delta_questions")),
        remind_1_us=_timeout(timeouts, "remind_1_us", 86_400_000_000),
        remind_2_us=_timeout(timeouts, "remind_2_us", 172_800_000_000),
        stall_us=_timeout(timeouts, "stall_us", 259_200_000_000),
        owner_digest_us=_timeout(timeouts, "owner_digest_us", 86_400_000_000),
        owner_wait_us=_timeout(timeouts, "owner_wait_us", 259_200_000_000),
        client_retry_s=retry_values,
        stall_after_s=int(loaded.get("stall_after_s") or 1800),
        source=path.name,
    )


def guide_payload(guide: OnboardingGuide, project_id: str, *, delta: bool) -> dict[str, Any]:
    """JSON guide for a bot. No file path and no secret."""
    questions = guide.delta_questions if delta else guide.questions
    return {
        "project_id": project_id,
        "version": guide.version,
        "guide_hash": guide.guide_hash,
        "integration_branch": guide.integration_branch,
        "workspace": guide.workspace,
        "repo": guide.repo,
        "host_id": guide.host_id,
        "rules": list(guide.rules),
        "questions": [
            {"id": question.id, "prompt": question.prompt, "required": question.required}
            for question in questions
        ],
        "delta": delta,
        "client_retry_s": list(guide.client_retry_s),
        "stall_after_s": guide.stall_after_s,
    }


def _resolve_path(project_id: str, guides_dir: str | None) -> Path:
    override = guides_dir if guides_dir is not None else os.environ.get(_GUIDES_ENV, "").strip()
    if override:
        candidate = Path(override) / f"{project_id}.yaml"
        if candidate.is_file():
            return candidate
    home_file = Path.home() / "dev" / "omnigent-agents" / project_id / "onboarding.yaml"
    if home_file.is_file():
        return home_file
    return _BUNDLED


def _questions(raw: object) -> tuple[GuideQuestion, ...]:
    if not isinstance(raw, list):
        return ()
    questions: list[GuideQuestion] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        questions.append(
            GuideQuestion(
                id=item["id"],
                prompt=str(item.get("prompt") or item["id"]),
                required=bool(item.get("required", True)),
                matches=_optional_str(item.get("matches")),
                matches_const=_optional_str(item.get("matches_const")),
            )
        )
    return tuple(questions)


def _str_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item.strip())


def _str_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)
    }


def _optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _timeout(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value
