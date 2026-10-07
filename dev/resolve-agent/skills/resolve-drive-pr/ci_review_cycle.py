"""CI review policy for reviewer failures and bounded minor-fix closeout."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile


ROUND_MARKER = "omnigent-resolve-review-round"


def load_helper(path: Path):
    spec = importlib.util.spec_from_file_location("selected_review_cycle", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import selected review helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_history(helper, repository: str, number: int) -> dict[str, set[str]]:
    """Count actual reviewed candidates and durable dispatch reservations on this PR."""
    history: dict[str, set[str]] = {}
    comments = helper.pages(
        f"repos/{repository}/issues/{number}/comments", helper.gh_json
    )
    for comment in comments:
        author = helper.actor(comment)
        body = str(comment.get("body") or "")
        if author in helper.REVIEW_BOTS:
            match = re.search(
                r"^<!-- polly-reviewed-sha: ([0-9a-f]{40}) -->$", body, re.M
            )
            if match and "<!-- polly-review-bot -->" in body.splitlines():
                history.setdefault(match[1], set()).add("polly")
        if author == helper.RESOLVE_BOT:
            match = re.fullmatch(
                rf"<!-- {ROUND_MARKER} head=([0-9a-f]{{40}}) reviewer=(polly|ocr) -->",
                body.strip(),
            )
            if match:
                history.setdefault(match[1], set()).add(match[2])
    # OCR review records bind their run marker to commit_id, including historical
    # reviews predating completion artifacts. Never accept human-supplied markers.
    for item in helper.pages(
        f"repos/{repository}/pulls/{number}/reviews", helper.gh_json
    ):
        head = item.get("commit_id") or ""
        if (
            helper.actor(item) == "github-actions[bot]"
            and re.fullmatch(r"[0-9a-f]{40}", head)
            and re.search(r"<!-- ocr-review-run:\d+-\d+ -->", item.get("body") or "")
        ):
            history.setdefault(head, set()).add("ocr")
    return history


def request_reviews(helper, repository: str, number: int) -> dict:
    # Serialize concurrent calls from the same Resolve runner. GitHub reservations
    # carry deduplication across the workflow's serialized checkpoint retries.
    lock = (
        Path(tempfile.gettempdir())
        / f"resolve-reviews-{repository.replace('/', '-')}-{number}.lock"
    )
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _request_reviews(helper, repository, number)


def _request_reviews(helper, repository: str, number: int) -> dict:
    state = helper.snapshot(repository, number)
    history = review_history(helper, repository, number)
    head = state["head_sha"]
    if len(history) > 5 or (head not in history and len(history) >= 5):
        raise ValueError(
            "Five review rounds exhausted: reassess blockers or finish minor notes without another review"
        )
    repo = helper.api_object(f"repos/{repository}", helper.gh_json)
    for reviewer, workflow in helper.REVIEWERS.items():
        if state["completed"][reviewer] or reviewer in history.get(head, set()):
            continue
        # Reserve before dispatch: retries and a timeout after GitHub accepted
        # the request must not issue the same reviewer request again.
        reservation = helper.gh_json(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repository}/issues/{number}/comments",
                "-f",
                f"body=<!-- {ROUND_MARKER} head={head} reviewer={reviewer} -->",
            ]
        )
        try:
            helper.gh_json(
                [
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/actions/workflows/{workflow}/dispatches",
                    "-f",
                    f"ref={repo['default_branch']}",
                    "-f",
                    f"inputs[pr]={number}",
                    "-f",
                    "inputs[force]=true",
                ]
            )
        except Exception as error:
            # Explicit API rejection means no run was accepted. Keep reservations
            # for transport errors and 5xx responses where acceptance is unknown.
            if getattr(error, "status", None) in {
                400,
                401,
                403,
                404,
                405,
                410,
                422,
                429,
            }:
                helper.gh_json(
                    [
                        "api",
                        "--method",
                        "DELETE",
                        f"repos/{repository}/issues/comments/{reservation['id']}",
                    ]
                )
            raise

    return state


def verify_run_target(repository: str, run: dict, number: int, head: str) -> None:
    """Bind a failed reviewer attempt to the PR and exact reviewed commit."""
    # Review workflows emit both fields together before launching the model,
    # so timeouts retain their identity without reaching the publication step.
    # workflow_dispatch.head_sha is the workflow source revision, not the PR.
    # Inspect the workflow's resolved inputs from this exact run attempt instead.
    result = subprocess.run(
        [
            "gh",
            "run",
            "view",
            str(run["id"]),
            "--repo",
            repository,
            "--attempt",
            str(run["run_attempt"]),
            "--log",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    headers: dict[str, dict] = {}
    bindings: set[tuple[str, str]] = set()
    observed: dict[str, set[str]] = {"pr": set(), "head": set()}
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"(.+\t.+\t)\ufeff?\d{4}-\d\d-\d\dT\S+Z (.*)", line)
        if not match:
            continue
        group, message = match.groups()
        if message.startswith("##[group]Run "):
            headers[group] = {"section": False, "pr": set(), "head": set()}
            continue
        header = headers.get(group)
        if header is None:
            continue
        if message == "##[endgroup]":
            for key in observed:
                observed[key].update(header[key])
            if len(header["pr"]) == len(header["head"]) == 1:
                bindings.add((next(iter(header["pr"])), next(iter(header["head"]))))
            del headers[group]
        elif message in {"env:", "with:"}:
            header["section"] = True
        elif not message.startswith("  "):
            header["section"] = False
        elif header["section"]:
            field = re.fullmatch(
                r"  (PR_NUMBER|INPUT_PR_NUMBER|pr_number|HEAD_SHA|INPUT_HEAD_SHA|head_sha): ([0-9a-f]+)",
                message,
            )
            if field:
                header["head" if "head" in field[1].lower() else "pr"].add(field[2])
    # Only runner input headers count, never review output. Reject conflicting
    # bindings too, so a printed imitation cannot override the real inputs.
    if bindings != {(str(number), head)} or observed != {
        "pr": {str(number)},
        "head": {head},
    }:
        raise ValueError("Review run does not establish the requested PR and commit")


def closeout_head(
    helper, repository: str, number: int, state: dict, handoff: dict
) -> str:
    closeout = handoff.get("review_closeout")
    if closeout is None:
        return ""
    rounds = handoff.get("review_rounds")
    if (
        not isinstance(closeout, dict)
        or not isinstance(rounds, list)
        or len(rounds) < 5
        or any(
            not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
            for sha in rounds
        )
        or len(set(rounds)) != len(rounds)
        or closeout.get("reviewed_head_sha") != rounds[-1]
        or not isinstance(closeout.get("reason"), str)
        or not closeout["reason"].strip()
    ):
        raise ValueError(
            "Minor-fix closeout requires at least five reviewed heads and an explanation"
        )
    history = review_history(helper, repository, number)
    if set(rounds) != set(history):
        raise ValueError("Review round history does not match GitHub evidence")
    source = rounds[-1]
    comparison = helper.api_object(
        f"repos/{repository}/compare/{source}...{state['head_sha']}", helper.gh_json
    )
    if comparison.get("status") not in {"ahead", "identical"}:
        raise ValueError("Final minor fixes must descend from the last reviewed head")
    return source


def check_review(helper, repository: str, number: int, handoff: dict) -> dict:
    state = helper.snapshot(repository, number)
    failures = handoff.get("review_failures") or {}
    if not isinstance(failures, dict):
        raise ValueError("review_failures must be an object keyed by reviewer")
    source = closeout_head(helper, repository, number, state, handoff)
    warnings = {}
    repo = (
        helper.api_object(f"repos/{repository}", helper.gh_json)
        if not all(state["completed"].values())
        else None
    )
    for reviewer, complete in state["completed"].items():
        if complete:
            continue
        if source and reviewer not in failures:
            completed = helper.completed_review_runs(
                repository,
                number,
                source,
                repo["default_branch"],
                reviewer,
                helper.gh_json,
            )
            if completed:
                run_id = max(
                    completed, key=lambda marker: int(marker.split("-")[0])
                ).split("-")[0]
                warnings[reviewer] = {
                    "run_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                    "reason": "Review budget reached; final minor fixes were tested but not re-reviewed. "
                    + handoff["review_closeout"]["reason"].strip(),
                }
                continue
        evidence = failures.get(reviewer)
        if not isinstance(evidence, dict):
            raise ValueError(f"{reviewer} review is incomplete; wait for its result")
        url = evidence.get("run_url") or ""
        match = re.fullmatch(
            rf"https://github\.com/{re.escape(repository)}/actions/runs/([1-9]\d*)", url
        )
        reason = evidence.get("reason")
        if (
            not match
            or evidence.get("head_sha")
            not in {state["head_sha"], source or state["head_sha"]}
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(
                f"{reviewer} failure requires current-head evidence and a run URL"
            )
        run = helper.api_object(
            f"repos/{repository}/actions/runs/{match[1]}", helper.gh_json
        )
        workflow = helper.api_object(
            f"repos/{repository}/actions/workflows/{helper.REVIEWERS[reviewer]}",
            helper.gh_json,
        )
        trusted = (
            run.get("head_branch") == repo["default_branch"]
            and run.get("event") in {"issue_comment", "workflow_dispatch"}
        ) or (reviewer == "ocr" and run.get("event") == "pull_request_target")
        if run.get("workflow_id") != workflow["id"] or not trusted:
            raise ValueError(
                f"{reviewer} failure must cite its trusted review workflow"
            )
        if run.get("status") != "completed":
            raise ValueError(f"{reviewer} review is still running; wait for its result")
        verify_run_target(repository, run, number, evidence["head_sha"])
        # OCR can finish with conclusion=success despite a failed finding filter.
        # This is a warning about unavailable coverage, never an approval receipt.
        warnings[reviewer] = {
            "run_url": url,
            "reason": reason.strip(),
            "workflow": workflow["name"],
        }
    # Waive only the completion requirement. The selected verifier still checks
    # the ORIGINAL snapshot fingerprint, current head, every disposition, and
    # empty remaining_work. Never fabricate a completed-review snapshot/receipt.
    helper.validate(
        {**state, "completed": dict.fromkeys(state["completed"], True)}, handoff
    )
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot", "request", "check"])
    parser.add_argument(
        "--helper", type=Path, default=Path(__file__).with_name("review_cycle_base.py")
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--handoff", type=Path)
    args = parser.parse_args()
    helper = load_helper(args.helper)
    if args.command == "check":
        if not args.handoff:
            parser.error("check requires --handoff")
        result = {
            "review_warnings": check_review(
                helper,
                args.repository,
                args.pr_number,
                json.loads(args.handoff.read_text()),
            )
        }
    elif args.command == "request":
        result = request_reviews(helper, args.repository, args.pr_number)
    else:
        result = helper.snapshot(args.repository, args.pr_number)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
