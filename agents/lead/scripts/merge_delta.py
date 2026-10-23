#!/usr/bin/env python3
"""Classify a new PR head as a full review, a base-merge delta, or a clean merge.

A base merge has two parents: the previous reviewed head, and a commit that
is already contained in the PR base. The review set is the conflict
resolution (``git show --remerge-diff``) plus files the PR itself changed
that the merge also changed. Anything else the base brought in stays
outside the review.

Stdout is one JSON object. Exit 0 means the classification is trustworthy.
``kind: full`` is the fallback, including when the previous Opus PASS head
is unknown. Exit 1 means the head could not be read; the lead treats that
as a full review and does not treat it as a clean merge.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

_OPUS_PASS_HEAD = re.compile(
    r"Opus (?:review|verdict) on (?:head|merge commit) `([0-9a-f]{7,40})`",
    re.IGNORECASE,
)
# Review comments bold the verdict as ``**PASS**`` or ``**PASS.**``.
_PASS_VERDICT = re.compile(r"\*\*PASS\.?\*\*")
_MAX_PAGES = 20


class GitError(Exception):
    """A git command failed."""


class GithubError(Exception):
    """GitHub CLI or API failed."""


def is_product_path(path: str) -> bool:
    """Product code is everything outside docs, specs, tests, markdown, and OpenAPI."""
    normalized = path.replace("\\", "/").lstrip("/")
    if normalized == "openapi.json" or normalized.endswith("/openapi.json"):
        return False
    if normalized.startswith(("docs/", "specs/", "tests/")):
        return False
    if normalized.endswith(".md"):
        return False
    return True


def last_opus_pass_sha(bodies: Sequence[str]) -> str | None:
    """Latest Opus PASS head mentioned in chronological comment bodies.

    Comments use ``Opus review on head `<sha>``` with a bold ``PASS``
    verdict (``**PASS**`` or ``**PASS.**``). A later BLOCK does not
    count, so the merge falls back to a full review.
    """
    found: str | None = None
    for body in bodies:
        match = _OPUS_PASS_HEAD.search(body)
        if match is None or _PASS_VERDICT.search(body) is None:
            continue
        found = match.group(1)
    return found


def resolve_last_reviewed(explicit: str | None, comments: Sequence[str]) -> str | None:
    """Prefer the lead's own SHA. Otherwise read it from Opus PASS comments."""
    if explicit and explicit.strip():
        return explicit.strip()
    return last_opus_pass_sha(comments)


def classify_merge(
    repo: Path,
    sha: str,
    base_ref: str,
    last_reviewed: str | None,
) -> dict[str, object]:
    """Return the review plan for ``sha``.

    ``product_code`` is true for a full review so a caller that only checks
    the flag still runs the tester.
    """
    head = _rev_parse(repo, sha)
    if head is None:
        raise GitError(f"cannot resolve head {sha}")
    parents = _parents(repo, head)
    base_sha = _resolve_base(repo, base_ref)
    reviewed = _rev_parse(repo, last_reviewed) if last_reviewed else None

    full = _plan(
        kind="full",
        reason="",
        sha=head,
        base_ref=base_ref,
        parents=parents,
        last_reviewed=reviewed,
        files=[],
        product_code=True,
    )
    if len(parents) != 2:
        reason = "not a merge commit" if len(parents) < 2 else "merge has more than two parents"
        return {**full, "reason": reason}
    if reviewed is None:
        return {**full, "reason": "no previous Opus PASS head"}
    parent_1, parent_2 = parents
    if parent_1 != reviewed:
        return {**full, "reason": "first parent is not the previous Opus PASS head"}
    if base_sha is None:
        return {**full, "reason": "base ref is not in this worktree"}
    if not _is_ancestor(repo, parent_2, base_sha):
        return {**full, "reason": "second parent is not contained in the PR base"}

    try:
        remerge = _names(
            repo,
            "show",
            "--remerge-diff",
            "--name-only",
            "--pretty=format:",
            "-z",
            head,
        )
    except GitError:
        return {**full, "reason": "remerge-diff unavailable"}

    merge_base = _merge_base(repo, parent_1, parent_2)
    if merge_base is None:
        return {**full, "reason": "no merge base between the two parents"}
    pr_files = _names(repo, "diff", "--name-only", "-z", merge_base, parent_1)
    brought_in = _names(repo, "diff", "--name-only", "-z", parent_1, head)
    delta = sorted(remerge | (pr_files & brought_in))
    if not delta:
        return _plan(
            kind="clean",
            reason="clean merge of the PR base",
            sha=head,
            base_ref=base_ref,
            parents=parents,
            last_reviewed=reviewed,
            files=[],
            product_code=False,
        )
    return _plan(
        kind="delta",
        reason="delta of the base merge",
        sha=head,
        base_ref=base_ref,
        parents=parents,
        last_reviewed=reviewed,
        files=delta,
        product_code=any(is_product_path(path) for path in delta),
    )


def _plan(
    *,
    kind: str,
    reason: str,
    sha: str,
    base_ref: str,
    parents: list[str],
    last_reviewed: str | None,
    files: list[str],
    product_code: bool,
) -> dict[str, object]:
    return {
        "kind": kind,
        "reason": reason,
        "sha": sha,
        "base_ref": base_ref,
        "parents": parents,
        "last_reviewed": last_reviewed,
        "files": files,
        "product_code": product_code,
    }


def _parents(repo: Path, sha: str) -> list[str]:
    line = _git_text(repo, "rev-list", "--parents", "-n", "1", sha)
    parts = line.split()
    if not parts or parts[0] != sha:
        raise GitError(f"cannot list parents of {sha}")
    return parts[1:]


def _resolve_base(repo: Path, base_ref: str) -> str | None:
    for candidate in (f"origin/{base_ref}", base_ref):
        resolved = _rev_parse(repo, candidate)
        if resolved is not None:
            return resolved
    return None


def _rev_parse(repo: Path, rev: str | None) -> str | None:
    if not rev:
        return None
    completed = _run_git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if completed.returncode != 0:
        return None
    return completed.stdout.decode().strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    completed = _run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise GitError(completed.stderr.decode("utf-8", "replace").strip())


def _merge_base(repo: Path, left: str, right: str) -> str | None:
    completed = _run_git(repo, "merge-base", left, right)
    if completed.returncode != 0:
        return None
    return completed.stdout.decode().strip() or None


def _names(repo: Path, *args: str) -> set[str]:
    completed = _run_git(repo, *args)
    if completed.returncode != 0:
        raise GitError(completed.stderr.decode("utf-8", "replace").strip())
    names: set[str] = set()
    for part in completed.stdout.split(b"\0"):
        if not part:
            continue
        names.add(part.decode("utf-8"))
    return names


def _git_text(repo: Path, *args: str) -> str:
    completed = _run_git(repo, *args)
    if completed.returncode != 0:
        raise GitError(completed.stderr.decode("utf-8", "replace").strip())
    return completed.stdout.decode().strip()


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        timeout=60,
    )


def fetch_issue_comments(repo: str, pr_number: int) -> list[str]:
    """Issue comments, oldest first. Review text on this fork is posted there."""
    bodies: list[str] = []
    for page in range(1, _MAX_PAGES + 1):
        payload = _gh_json(
            [
                "api",
                (
                    f"repos/{repo}/issues/{pr_number}/comments"
                    f"?per_page=100&page={page}&sort=created&direction=asc"
                ),
            ]
        )
        if not isinstance(payload, list):
            raise GithubError("issue comments response was not a list")
        if not payload:
            break
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("body"), str):
                bodies.append(item["body"])
        if len(payload) < 100:
            break
    return bodies


def _gh_json(args: list[str]) -> object:
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
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


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", default=".", help="Worktree that contains the head")
    parser.add_argument("--sha", required=True, help="Head commit to classify")
    parser.add_argument("--base", required=True, help="PR baseRefName, such as epic/team-chat")
    parser.add_argument("--last-reviewed", default="", help="Previous Opus PASS SHA, if known")
    parser.add_argument("--pr", type=int, default=0, help="PR number, to read Opus PASS comments")
    parser.add_argument("--repo", default="", help="owner/name, required to read comments")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    comments: list[str] = []
    if not args.last_reviewed and args.pr and args.repo:
        try:
            comments = fetch_issue_comments(args.repo, args.pr)
        except GithubError as exc:
            print(f"merge_delta: comment lookup failed: {exc}", file=sys.stderr)
    last = resolve_last_reviewed(args.last_reviewed, comments)
    try:
        plan = classify_merge(Path(args.repo_dir), args.sha, args.base, last)
    except GitError as exc:
        json.dump(
            {
                "kind": "full",
                "reason": str(exc),
                "sha": args.sha,
                "base_ref": args.base,
                "parents": [],
                "last_reviewed": None,
                "files": [],
                "product_code": True,
                "error": True,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 1
    json.dump(plan, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
