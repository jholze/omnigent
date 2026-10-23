#!/usr/bin/env python3
"""Wait until required Fork CI is green on one pull-request head.

Exit codes for the lead and for reviewers:

  0  green — every required check succeeded on this SHA, and it is still the head
  1  error — the SHA, the PR, or the GitHub API could not be read; do not review
  2  red — a required check completed without success; do not review
  3  superseded — the PR head moved; wait on the new SHA, do not review this one
  4  waiting — required checks are not green yet
  5  draft — Fork CI does not run on drafts; mark the PR ready, then wait again

Stdout is one JSON object. The required jobs are the three Fork CI jobs in
``.github/workflows/fork-ci.yml``. A ruleset may add checks. It cannot drop
these three.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

# Job names from .github/workflows/fork-ci.yml. E2E UI is continue-on-error
# and is intentionally absent.
REQUIRED_CHECK_NAMES: tuple[str, ...] = (
    "Python lint (ruff, pyrefly)",
    "Pytest (SQLite)",
    "Web (lint, typecheck, vitest, build)",
)

EXIT_GREEN = 0
EXIT_ERROR = 1
EXIT_RED = 2
EXIT_SUPERSEDED = 3
EXIT_WAITING = 4
EXIT_DRAFT = 5

_EXIT_FOR_VERDICT = {
    "green": EXIT_GREEN,
    "error": EXIT_ERROR,
    "red": EXIT_RED,
    "superseded": EXIT_SUPERSEDED,
    "waiting": EXIT_WAITING,
    "draft": EXIT_DRAFT,
}

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DEFAULT_INTERVAL_S = 60.0
_DEFAULT_MAX_WAIT_S = 4 * 60 * 60.0
_MAX_PAGES = 20


class GithubError(Exception):
    """GitHub CLI or API failed."""


@dataclass(frozen=True)
class CheckRun:
    id: int
    name: str
    status: str
    conclusion: str | None
    head_sha: str


@dataclass(frozen=True)
class PrSnapshot:
    head_sha: str
    base_ref: str
    draft: bool


@dataclass(frozen=True)
class GateResult:
    verdict: str
    sha: str
    pr_head: str
    base_ref: str
    required: tuple[str, ...]
    checks: dict[str, dict[str, object]]
    message: str

    def exit_code(self) -> int:
        return _EXIT_FOR_VERDICT[self.verdict]

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "sha": self.sha,
            "pr_head": self.pr_head,
            "base_ref": self.base_ref,
            "required": list(self.required),
            "checks": self.checks,
            "message": self.message,
        }


class Transport(Protocol):
    def pull_request(self, repo: str, number: int) -> PrSnapshot: ...

    def check_runs(self, repo: str, sha: str) -> list[CheckRun]: ...

    def branch_rules(self, repo: str, branch: str) -> list[Mapping[str, object]]: ...


def normalize_sha(sha: str) -> str:
    """Return a 40-character lowercase SHA, or raise ValueError."""
    lowered = sha.strip().lower()
    if _SHA.fullmatch(lowered) is None:
        raise ValueError("SHA must be 40 hex characters")
    return lowered


def normalize_repo(repo: str) -> str:
    """Return owner/name, or raise ValueError."""
    cleaned = repo.strip()
    if _REPO.fullmatch(cleaned) is None:
        raise ValueError("repo must be owner/name")
    return cleaned


def effective_required(rules: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Fork CI's three jobs, plus any contexts a ruleset adds.

    A missing or narrower ruleset must not drop a required job.
    """
    extra: list[str] = []
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters")
        if not isinstance(params, Mapping):
            continue
        checks = params.get("required_status_checks") or []
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            context = check.get("context")
            if isinstance(context, str) and context:
                extra.append(context)
    return tuple(dict.fromkeys((*REQUIRED_CHECK_NAMES, *extra)))


def judge(
    runs: Sequence[CheckRun],
    sha: str,
    pr_head: str,
    required: Sequence[str],
) -> tuple[str, dict[str, dict[str, object]]]:
    """Decide whether this SHA may be reviewed.

    A moved head wins over a red or green result: the new head needs its
    own checks. A completed non-success wins over a check that has not
    finished, so a known failure does not sit in the wait loop.
    """
    if not required:
        raise ValueError("required checks must not be empty")
    newest: dict[str, CheckRun] = {}
    for run in runs:
        if run.head_sha.lower() != sha:
            continue
        current = newest.get(run.name)
        if current is None or run.id > current.id:
            newest[run.name] = run

    if pr_head.lower() != sha:
        return "superseded", _check_details(required, newest)

    red = False
    waiting = False
    for name in required:
        run = newest.get(name)
        if run is None or run.status != "completed":
            waiting = True
            continue
        if run.conclusion != "success":
            red = True
    details = _check_details(required, newest)
    if red:
        return "red", details
    if waiting:
        return "waiting", details
    return "green", details


def _check_details(
    required: Sequence[str], newest: Mapping[str, CheckRun]
) -> dict[str, dict[str, object]]:
    details: dict[str, dict[str, object]] = {}
    for name in required:
        run = newest.get(name)
        if run is None:
            details[name] = {"status": "missing", "conclusion": None, "id": None}
            continue
        details[name] = {
            "status": run.status,
            "conclusion": run.conclusion,
            "id": run.id,
        }
    return details


def wait_for_ci(
    *,
    repo: str,
    pr_number: int,
    sha: str,
    transport: Transport,
    once: bool = False,
    interval_s: float = _DEFAULT_INTERVAL_S,
    max_wait_s: float = _DEFAULT_MAX_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> GateResult:
    """Poll until the SHA is reviewable, the head moves, or the wait ends."""
    repo = normalize_repo(repo)
    sha = normalize_sha(sha)
    if pr_number <= 0:
        raise ValueError("PR number must be positive")
    if interval_s <= 0 or max_wait_s < 0:
        raise ValueError("interval must be positive and max wait must be >= 0")

    deadline = clock() + max_wait_s
    while True:
        try:
            result = _evaluate(repo, pr_number, sha, transport)
            if result.verdict == "green":
                confirmed = transport.pull_request(repo, pr_number)
                if confirmed.head_sha.lower() != sha:
                    return GateResult(
                        verdict="superseded",
                        sha=sha,
                        pr_head=confirmed.head_sha.lower(),
                        base_ref=confirmed.base_ref,
                        required=result.required,
                        checks=result.checks,
                        message=(
                            f"PR head moved from {sha} to {confirmed.head_sha.lower()} "
                            "after the checks were read"
                        ),
                    )
                return result
        except GithubError as exc:
            now = clock()
            if once or now >= deadline:
                return GateResult(
                    verdict="error",
                    sha=sha,
                    pr_head="",
                    base_ref="",
                    required=REQUIRED_CHECK_NAMES,
                    checks={},
                    message=str(exc),
                )
            sleep(min(interval_s, max(0.0, deadline - now)))
            continue
        if result.verdict != "waiting":
            return result
        now = clock()
        if once or now >= deadline:
            message = result.message
            if not once and now >= deadline:
                message = f"timed out after {int(max_wait_s)}s waiting for required CI on {sha}"
            return GateResult(
                verdict="waiting",
                sha=sha,
                pr_head=result.pr_head,
                base_ref=result.base_ref,
                required=result.required,
                checks=result.checks,
                message=message,
            )
        sleep(min(interval_s, max(0.0, deadline - now)))


def _evaluate(repo: str, pr_number: int, sha: str, transport: Transport) -> GateResult:
    pr = transport.pull_request(repo, pr_number)
    pr_head = pr.head_sha.lower()
    if pr.draft:
        return GateResult(
            verdict="draft",
            sha=sha,
            pr_head=pr_head,
            base_ref=pr.base_ref,
            required=REQUIRED_CHECK_NAMES,
            checks={},
            message="PR is a draft, so Fork CI does not run; mark it ready and wait again",
        )
    try:
        rules = list(transport.branch_rules(repo, pr.base_ref))
    except GithubError:
        rules = []
    required = effective_required(rules)
    runs = transport.check_runs(repo, sha)
    verdict, checks = judge(runs, sha, pr_head, required)
    return GateResult(
        verdict=verdict,
        sha=sha,
        pr_head=pr_head,
        base_ref=pr.base_ref,
        required=required,
        checks=checks,
        message=_message(verdict, sha, pr_head, checks),
    )


def _message(
    verdict: str,
    sha: str,
    pr_head: str,
    checks: Mapping[str, Mapping[str, object]],
) -> str:
    if verdict == "green":
        return f"required CI is green on {sha}"
    if verdict == "superseded":
        return f"PR head is {pr_head}, not {sha}"
    if verdict == "red":
        failed = [
            name
            for name, detail in checks.items()
            if detail.get("status") == "completed" and detail.get("conclusion") != "success"
        ]
        listed = ", ".join(failed) if failed else "a required check"
        return f"required CI is not green on {sha}: {listed}"
    return f"required CI is not green yet on {sha}"


class GhCliTransport:
    """Read-only GitHub CLI adapter."""

    def pull_request(self, repo: str, number: int) -> PrSnapshot:
        payload = _gh_json(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "headRefOid,baseRefName,isDraft",
            ]
        )
        if not isinstance(payload, dict):
            raise GithubError("pull request response was not an object")
        head = payload.get("headRefOid")
        base = payload.get("baseRefName")
        draft = payload.get("isDraft")
        if not isinstance(head, str) or not isinstance(base, str) or not isinstance(draft, bool):
            raise GithubError("pull request response missed head, base, or draft")
        return PrSnapshot(head_sha=head, base_ref=base, draft=draft)

    def check_runs(self, repo: str, sha: str) -> list[CheckRun]:
        runs: list[CheckRun] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = _gh_json(
                [
                    "api",
                    f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}",
                ]
            )
            if not isinstance(payload, dict):
                raise GithubError("check-runs response was not an object")
            batch = payload.get("check_runs") or []
            if not isinstance(batch, list):
                raise GithubError("check-runs response missed check_runs")
            for item in batch:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                status = item.get("status")
                run_id = item.get("id")
                head = item.get("head_sha") or sha
                conclusion = item.get("conclusion")
                if not isinstance(name, str) or not isinstance(status, str):
                    continue
                if not isinstance(run_id, int) or not isinstance(head, str):
                    continue
                if conclusion is not None and not isinstance(conclusion, str):
                    conclusion = None
                runs.append(
                    CheckRun(
                        id=run_id,
                        name=name,
                        status=status,
                        conclusion=conclusion,
                        head_sha=head,
                    )
                )
            if len(batch) < 100:
                break
        return runs

    def branch_rules(self, repo: str, branch: str) -> list[Mapping[str, object]]:
        quoted = urllib.parse.quote(branch, safe="")
        payload = _gh_json(["api", f"repos/{repo}/rules/branches/{quoted}"])
        if not isinstance(payload, list):
            raise GithubError("branch rules response was not a list")
        return [item for item in payload if isinstance(item, dict)]


def _gh_json(args: list[str]) -> object:
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=_gh_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GithubError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise GithubError(detail or f"gh exited {completed.returncode}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GithubError("gh returned non-JSON output") from exc


def _gh_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub repo, owner/name")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument("--sha", required=True, help="40-character head SHA to gate")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit. Reviewers use this. The lead omits it and polls.",
    )
    parser.add_argument("--interval", type=float, default=_DEFAULT_INTERVAL_S)
    parser.add_argument("--max-wait", type=float, default=_DEFAULT_MAX_WAIT_S)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = wait_for_ci(
            repo=args.repo,
            pr_number=args.pr,
            sha=args.sha,
            transport=GhCliTransport(),
            once=args.once,
            interval_s=args.interval,
            max_wait_s=args.max_wait,
        )
    except (GithubError, ValueError) as exc:
        result = GateResult(
            verdict="error",
            sha=args.sha.strip().lower(),
            pr_head="",
            base_ref="",
            required=REQUIRED_CHECK_NAMES,
            checks={},
            message=str(exc),
        )
    json.dump(result.as_dict(), sys.stdout)
    sys.stdout.write("\n")
    return result.exit_code()


if __name__ == "__main__":
    sys.exit(main())
