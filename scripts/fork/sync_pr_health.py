"""Classify the open upstream-sync PR for ``scripts/fork/upstream-sync.sh``.

Reads ``gh pr list --json number,headRefName,createdAt,mergeable,
statusCheckRollup,url`` on stdin. When a ``sync/upstream-*`` PR is open, prints
one line ``<state>\\t<summary>\\t<pr json>`` for the oldest one, where state is
``blocked`` (older than the stale limit, conflicting, or a required check
failed) or ``open``. Prints nothing when no sync PR is open.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any

REQUIRED_CHECKS = (
    "Python lint (ruff, pyrefly)",
    "Web (lint, typecheck, vitest, build)",
    "Pytest (SQLite)",
)
_FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}


def failing_checks(rollup: list[dict[str, Any]] | None, required: tuple[str, ...]) -> list[str]:
    """Return the required check names whose latest result failed.

    :param rollup: ``statusCheckRollup`` entries; check runs carry ``name`` and
        ``conclusion``, commit statuses carry ``context`` and ``state``.
    :param required: Check names that block a merge.
    :returns: Sorted failing required check names.
    """
    failed = set()
    for entry in rollup or []:
        name = entry.get("name") or entry.get("context") or ""
        result = (entry.get("conclusion") or entry.get("state") or "").upper()
        if name in required and result in _FAILED:
            failed.add(name)
    return sorted(failed)


def classify(
    prs: list[dict[str, Any]],
    *,
    now: dt.datetime,
    stale_days: float,
    required: tuple[str, ...] = REQUIRED_CHECKS,
) -> tuple[str, str, dict[str, Any]] | None:
    """Classify the oldest open sync PR.

    :param prs: Open pull requests as returned by ``gh pr list --json``.
    :param now: Current time (timezone-aware).
    :param stale_days: Age in days after which an open sync PR counts as blocked.
    :param required: Check names that block a merge.
    :returns: ``(state, summary, details)`` or ``None`` when no sync PR is open.
    """
    sync = [p for p in prs if str(p.get("headRefName", "")).startswith("sync/upstream")]
    if not sync:
        return None
    pr = min(sync, key=lambda p: (p.get("createdAt") or "", p.get("number") or 0))
    created = dt.datetime.fromisoformat(str(pr["createdAt"]).replace("Z", "+00:00"))
    age_days = (now - created).total_seconds() / 86400
    reasons = []
    if age_days > stale_days:
        reasons.append(f"open for {int(age_days)} days (limit {stale_days:g})")
    if pr.get("mergeable") == "CONFLICTING":
        reasons.append("has merge conflicts")
    failed = failing_checks(pr.get("statusCheckRollup"), required)
    if failed:
        reasons.append("failing checks: " + ", ".join(failed))
    label = f"#{pr.get('number')} {pr.get('headRefName')}"
    details = {
        "number": pr.get("number"),
        "branch": pr.get("headRefName"),
        "url": pr.get("url"),
        "created_at": pr.get("createdAt"),
        "age_days": round(age_days, 1),
        "mergeable": pr.get("mergeable"),
        "failing_checks": failed,
        "reasons": reasons,
    }
    if reasons:
        return "blocked", f"{label} " + "; ".join(reasons), details
    return "open", label, details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stale-days", type=float, default=7.0)
    args = parser.parse_args(argv)
    raw = sys.stdin.read().strip()
    prs = json.loads(raw) if raw else []
    result = classify(prs, now=dt.datetime.now(dt.UTC), stale_days=args.stale_days)
    if result is not None:
        state, summary, details = result
        print(f"{state}\t{summary}\t{json.dumps(details)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
