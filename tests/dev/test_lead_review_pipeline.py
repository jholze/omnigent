"""CI gate and base-merge delta for the Omnigent lead review pipeline."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SHA = "a" * 40
_OTHER = "b" * 40
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ci_gate = _load("lead_ci_gate", _ROOT / "agents/lead/scripts/ci_gate.py")
merge_delta = _load("lead_merge_delta", _ROOT / "agents/lead/scripts/merge_delta.py")


def _prompt(path: Path) -> str:
    document = yaml.safe_load(path.read_text())
    prompt = document["prompt"]
    assert isinstance(prompt, str)
    return prompt


def _check(
    name: str,
    run_id: int,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    sha: str = _SHA,
) -> object:
    return ci_gate.CheckRun(
        id=run_id,
        name=name,
        status=status,
        conclusion=conclusion,
        head_sha=sha,
    )


def _required_runs(*, conclusion: str = "success", status: str = "completed", sha: str = _SHA):
    return [
        _check(name, index + 1, status=status, conclusion=conclusion, sha=sha)
        for index, name in enumerate(ci_gate.REQUIRED_CHECK_NAMES)
    ]


class _Pull:
    def __init__(self, head: str, *, draft: bool = False, base: str = "epic/team-chat") -> None:
        self.head_sha = head
        self.base_ref = base
        self.draft = draft


class _Github:
    def __init__(
        self,
        heads: list[_Pull],
        runs: list,
        rules: list | Exception | None = None,
    ) -> None:
        self.heads = list(heads)
        self.runs = runs
        self.rules = [] if rules is None else rules
        self.check_calls = 0

    def pull_request(self, repo: str, number: int) -> _Pull:
        if not self.heads:
            raise AssertionError("pull_request called with no snapshots left")
        current = self.heads.pop(0)
        if not self.heads:
            self.heads.append(current)
        return current

    def check_runs(self, repo: str, sha: str) -> list:
        self.check_calls += 1
        return list(self.runs)

    def branch_rules(self, repo: str, branch: str) -> list:
        if isinstance(self.rules, Exception):
            raise self.rules
        return list(self.rules)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed


def _init(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "epic/team-chat")
    return path


def _write(repo: Path, relative: str, text: str) -> None:
    destination = repo / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_required_jobs_stay_required_when_a_ruleset_is_narrower() -> None:
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "Pytest (SQLite)"}]},
        }
    ]
    required = ci_gate.effective_required(rules)
    assert required[:3] == ci_gate.REQUIRED_CHECK_NAMES
    assert "E2E UI (Playwright, non-blocking)" not in required


def test_ruleset_can_only_add_checks() -> None:
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "Extra"}]},
        }
    ]
    assert ci_gate.effective_required(rules)[-1] == "Extra"
    assert ci_gate.effective_required([]) == ci_gate.REQUIRED_CHECK_NAMES


def test_green_requires_every_fork_ci_job_on_that_sha() -> None:
    runs = _required_runs()
    runs.append(_check("E2E UI (Playwright, non-blocking)", 99, conclusion="failure"))
    verdict, _details = ci_gate.judge(runs, _SHA, _SHA, ci_gate.REQUIRED_CHECK_NAMES)
    assert verdict == "green"


def test_failure_stops_the_wait_while_another_job_is_still_running() -> None:
    runs = _required_runs()
    runs[1] = _check(ci_gate.REQUIRED_CHECK_NAMES[1], 50, conclusion="failure")
    runs[2] = _check(ci_gate.REQUIRED_CHECK_NAMES[2], 51, status="in_progress", conclusion=None)
    verdict, _details = ci_gate.judge(runs, _SHA, _SHA, ci_gate.REQUIRED_CHECK_NAMES)
    assert verdict == "red"


def test_newest_check_run_wins() -> None:
    name = ci_gate.REQUIRED_CHECK_NAMES[0]
    runs = _required_runs()
    runs.append(_check(name, 1, conclusion="failure"))
    runs.append(_check(name, 80, conclusion="success"))
    verdict, _details = ci_gate.judge(runs, _SHA, _SHA, ci_gate.REQUIRED_CHECK_NAMES)
    assert verdict == "green"
    runs.append(_check(name, 90, conclusion="cancelled"))
    verdict, _details = ci_gate.judge(runs, _SHA, _SHA, ci_gate.REQUIRED_CHECK_NAMES)
    assert verdict == "red"


def test_moved_head_is_superseded_even_when_the_old_sha_is_green() -> None:
    verdict, _details = ci_gate.judge(_required_runs(), _SHA, _OTHER, ci_gate.REQUIRED_CHECK_NAMES)
    assert verdict == "superseded"


def test_missing_or_in_progress_checks_wait() -> None:
    waiting, _details = ci_gate.judge(
        _required_runs()[:-1], _SHA, _SHA, ci_gate.REQUIRED_CHECK_NAMES
    )
    assert waiting == "waiting"
    in_progress, _details = ci_gate.judge(
        _required_runs(status="in_progress", conclusion=None),
        _SHA,
        _SHA,
        ci_gate.REQUIRED_CHECK_NAMES,
    )
    assert in_progress == "waiting"


def test_wait_polls_until_green_and_rechecks_the_head() -> None:
    github = _Github([_Pull(_SHA), _Pull(_SHA), _Pull(_SHA)], _required_runs())
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=113,
        sha=_SHA,
        transport=github,
        interval_s=60,
        max_wait_s=1000,
        sleep=sleep,
        clock=lambda: clock["now"],
    )
    assert result.verdict == "green"
    assert result.exit_code() == 0
    assert clock["now"] == 0.0


def test_green_result_is_superseded_when_the_head_moves_before_review() -> None:
    github = _Github([_Pull(_SHA), _Pull(_OTHER)], _required_runs())
    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=113,
        sha=_SHA,
        transport=github,
        once=True,
    )
    assert result.verdict == "superseded"
    assert result.exit_code() == 3
    assert result.pr_head == _OTHER


def test_once_does_not_poll_and_a_draft_does_not_read_checks() -> None:
    github = _Github([_Pull(_SHA, draft=True)], [])
    slept = False

    def sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True

    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=113,
        sha=_SHA,
        transport=github,
        once=True,
        sleep=sleep,
    )
    assert result.verdict == "draft"
    assert result.exit_code() == 5
    assert github.check_calls == 0
    assert not slept


def test_timeout_and_transient_api_errors() -> None:
    github = _Github([_Pull(_SHA)], _required_runs(status="queued", conclusion=None))
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=113,
        sha=_SHA,
        transport=github,
        interval_s=10,
        max_wait_s=15,
        sleep=sleep,
        clock=lambda: clock["now"],
    )
    assert result.verdict == "waiting"
    assert result.exit_code() == 4
    assert "timed out" in result.message

    class _Flaky(_Github):
        def __init__(self) -> None:
            super().__init__([_Pull(_SHA), _Pull(_SHA)], _required_runs())
            self.failed = False

        def check_runs(self, repo: str, sha: str) -> list:
            if not self.failed:
                self.failed = True
                raise ci_gate.GithubError("temporary")
            return super().check_runs(repo, sha)

    flaky = _Flaky()
    clock["now"] = 0.0
    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=113,
        sha=_SHA,
        transport=flaky,
        interval_s=5,
        max_wait_s=30,
        sleep=sleep,
        clock=lambda: clock["now"],
    )
    assert result.verdict == "green"


def test_rules_api_failure_still_requires_the_three_jobs() -> None:
    github = _Github(
        [_Pull(_SHA), _Pull(_SHA)],
        _required_runs(),
        rules=ci_gate.GithubError("404"),
    )
    result = ci_gate.wait_for_ci(
        repo="jholze/omnigent",
        pr_number=112,
        sha=_SHA,
        transport=github,
        once=True,
    )
    assert result.verdict == "green"
    assert result.required == ci_gate.REQUIRED_CHECK_NAMES


def test_cli_rejects_a_short_sha(capsys: pytest.CaptureFixture[str]) -> None:
    code = ci_gate.main(
        ["--repo", "jholze/omnigent", "--pr", "112", "--sha", "951cff036", "--once"]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "error"


def test_cli_reads_check_runs_for_the_exact_sha(tmp_path: Path) -> None:
    log = tmp_path / "gh.log"
    fake = tmp_path / "gh"
    names = list(ci_gate.REQUIRED_CHECK_NAMES)
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"log = {str(log)!r}\n"
        "args = sys.argv[1:]\n"
        "with open(log, 'a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['pr', 'view']:\n"
        "    json.dump({\n"
        f"        'headRefOid': '{_SHA}',\n"
        "        'baseRefName': 'epic/team-chat',\n"
        "        'isDraft': False,\n"
        "    }, sys.stdout)\n"
        "    raise SystemExit(0)\n"
        "if args[0] == 'api' and 'check-runs' in args[1]:\n"
        f"    names = {names!r}\n"
        "    json.dump({'check_runs': [\n"
        "        {'id': index + 1, 'name': name, 'status': 'completed',\n"
        f"         'conclusion': 'success', 'head_sha': '{_SHA}'}}\n"
        "        for index, name in enumerate(names)\n"
        "    ]}, sys.stdout)\n"
        "    raise SystemExit(0)\n"
        "if args[0] == 'api' and 'rules/branches' in args[1]:\n"
        "    sys.stderr.write('not found')\n"
        "    raise SystemExit(1)\n"
        "sys.stderr.write('unexpected')\n"
        "raise SystemExit(2)\n"
    )
    fake.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    completed = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "agents/lead/scripts/ci_gate.py"),
            "--repo",
            "jholze/omnigent",
            "--pr",
            "112",
            "--sha",
            _SHA,
            "--once",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "green"
    assert payload["sha"] == _SHA
    recorded = [json.loads(line) for line in log.read_text().splitlines()]
    assert any("epic%2Fteam-chat" in part for command in recorded for part in command)


@pytest.mark.parametrize(
    ("path", "product"),
    [
        ("omnigent/server/routes/chat.py", True),
        ("agents/lead/config.yaml", True),
        (".github/workflows/fork-ci.yml", True),
        ("docs/fork-workflow.md", False),
        ("specs/omnigent-chat/04-requirements.md", False),
        ("tests/test_repro_environment.py", False),
        ("openapi.json", False),
        ("web/openapi.json", False),
        ("README.md", False),
    ],
)
def test_product_paths_are_the_ones_outside_docs_and_tests(path: str, product: bool) -> None:
    assert merge_delta.is_product_path(path) is product


def test_last_opus_pass_ignores_later_blocks_and_ci_comments() -> None:
    previous = "0b15b7f90ee2a217a1a4ce8858be7ca716afc3b0"
    blocked = "c" * 40
    bodies = [
        (
            f"Opus review on head `{previous}` (session `abc`).\n\n"
            "**PASS.** The commit is the four reviewed files."
        ),
        "Required CI on head `29f96a16f` is green. Opus and DeepSeek are both PASS.",
        f"Opus review on head `{blocked}`.\n\n**BLOCK.**",
        "Opus verdict on merge commit `29f96a16f` is **PASS**.",
    ]
    assert merge_delta.last_opus_pass_sha(bodies) == "29f96a16f"
    assert merge_delta.last_opus_pass_sha(bodies[:3]) == previous
    assert merge_delta.resolve_last_reviewed(previous, bodies) == previous


def _clean_merge(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _init(tmp_path / "repo")
    _write(repo, "omnigent/a.py", "a = 1\n")
    _commit(repo, "base")
    _git(repo, "branch", "feature")
    _write(repo, "omnigent/b.py", "b = 1\n")
    _commit(repo, "base moves")
    _git(repo, "checkout", "feature")
    _write(repo, "omnigent/a.py", "a = 2\n")
    reviewed = _commit(repo, "pr work")
    _git(repo, "merge", "epic/team-chat", "-m", "Merge epic/team-chat into feature")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, reviewed, head


def test_clean_base_merge_carries_the_previous_verdict(tmp_path: Path) -> None:
    repo, reviewed, head = _clean_merge(tmp_path)
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed)
    assert plan["kind"] == "clean"
    assert plan["files"] == []
    assert plan["product_code"] is False
    assert plan["last_reviewed"] == reviewed


def test_overlapping_auto_merge_reviews_only_the_shared_file(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "omnigent/a.py", "line1\nline2\nline3\n")
    _commit(repo, "base")
    _git(repo, "branch", "feature")
    _write(repo, "omnigent/a.py", "line1\nline2\nbase-change\n")
    _write(repo, "omnigent/c.py", "from_base = 1\n")
    _commit(repo, "base moves")
    _git(repo, "checkout", "feature")
    _write(repo, "omnigent/a.py", "pr-change\nline2\nline3\n")
    reviewed = _commit(repo, "pr work")
    _git(repo, "merge", "epic/team-chat", "-m", "Merge epic/team-chat into feature")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed[:12])
    assert plan["kind"] == "delta"
    assert plan["files"] == ["omnigent/a.py"]
    assert "omnigent/c.py" not in plan["files"]
    assert plan["product_code"] is True


def test_evil_merge_outside_the_conflict_is_still_a_product_delta(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "omnigent/a.py", "a = 1\n")
    _commit(repo, "base")
    _git(repo, "branch", "feature")
    _write(repo, "omnigent/b.py", "b = 1\n")
    _commit(repo, "base moves")
    _git(repo, "checkout", "feature")
    _write(repo, "omnigent/a.py", "a = 2\n")
    _write(repo, "tests/t.py", "def test_ok():\n    assert True\n")
    reviewed = _commit(repo, "pr work")
    _git(repo, "merge", "--no-commit", "epic/team-chat")
    _write(repo, "omnigent/evil.py", "evil = 1\n")
    _git(repo, "add", "omnigent/evil.py")
    _git(repo, "commit", "-m", "Merge epic/team-chat into feature")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed)
    assert plan["kind"] == "delta"
    assert plan["files"] == ["omnigent/evil.py"]
    assert "omnigent/b.py" not in plan["files"]
    assert plan["product_code"] is True


def test_docs_only_resolution_does_not_require_the_tester(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "docs/guide.md", "guide\n")
    _commit(repo, "base")
    _git(repo, "branch", "feature")
    _write(repo, "docs/guide.md", "guide from base\n")
    _commit(repo, "base moves")
    _git(repo, "checkout", "feature")
    _write(repo, "docs/guide.md", "guide from pr\n")
    reviewed = _commit(repo, "pr work")
    conflict = _git(repo, "merge", "epic/team-chat", "-m", "merge", check=False)
    assert conflict.returncode != 0
    _write(repo, "docs/guide.md", "guide from base\nguide from pr\n")
    _git(repo, "add", "docs/guide.md")
    _git(repo, "commit", "-m", "Merge epic/team-chat into feature")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed)
    assert plan["kind"] == "delta"
    assert plan["files"] == ["docs/guide.md"]
    assert plan["product_code"] is False


def test_non_base_merges_and_unknown_history_stay_a_full_review(tmp_path: Path) -> None:
    repo, reviewed, head = _clean_merge(tmp_path)
    ordinary = merge_delta.classify_merge(repo, reviewed, "epic/team-chat", reviewed)
    assert ordinary["kind"] == "full"
    assert ordinary["product_code"] is True

    unknown = merge_delta.classify_merge(repo, head, "epic/team-chat", None)
    assert unknown["kind"] == "full"
    assert unknown["reason"] == "no previous Opus PASS head"

    mismatch = merge_delta.classify_merge(repo, head, "epic/team-chat", "c" * 40)
    assert mismatch["kind"] == "full"

    missing_base = merge_delta.classify_merge(repo, head, "epic/does-not-exist", reviewed)
    assert missing_base["kind"] == "full"
    assert missing_base["reason"] == "base ref is not in this worktree"

    _git(repo, "checkout", "-b", "other", "epic/team-chat")
    _write(repo, "omnigent/other.py", "other = 1\n")
    _commit(repo, "side branch")
    _git(repo, "checkout", "feature")
    _git(repo, "merge", "other", "-m", "Merge other into feature")
    side = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # The feature tip's first parent is the clean merge, which is not `reviewed`.
    foreign = merge_delta.classify_merge(repo, side, "epic/team-chat", reviewed)
    assert foreign["kind"] == "full"


def test_merge_from_outside_the_base_is_a_full_review(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "omnigent/a.py", "a = 1\n")
    _commit(repo, "base")
    _git(repo, "branch", "feature")
    _git(repo, "checkout", "-b", "side")
    _write(repo, "omnigent/side.py", "side = 1\n")
    _commit(repo, "side work")
    _git(repo, "checkout", "feature")
    _write(repo, "omnigent/a.py", "a = 2\n")
    reviewed = _commit(repo, "pr work")
    _git(repo, "merge", "side", "-m", "Merge side into feature")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed)
    assert plan["kind"] == "full"
    assert plan["reason"] == "second parent is not contained in the PR base"


def test_remerge_diff_failure_falls_back_to_a_full_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, reviewed, head = _clean_merge(tmp_path)
    original = merge_delta._names

    def _names(worktree: Path, *args: str) -> set[str]:
        if args and args[0] == "show":
            raise merge_delta.GitError("remerge-diff unavailable")
        return original(worktree, *args)

    monkeypatch.setattr(merge_delta, "_names", _names)
    plan = merge_delta.classify_merge(repo, head, "epic/team-chat", reviewed)
    assert plan["kind"] == "full"
    assert plan["reason"] == "remerge-diff unavailable"
    assert plan["product_code"] is True


def test_unreadable_head_is_not_a_clean_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "omnigent/a.py", "a = 1\n")
    _commit(repo, "base")
    code = merge_delta.main(
        ["--repo-dir", str(repo), "--sha", "deadbeef", "--base", "epic/team-chat"]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "full"
    assert payload["product_code"] is True
    assert payload["error"] is True


def test_cli_uses_the_opus_pass_comment_when_the_lead_has_no_sha(
    tmp_path: Path,
) -> None:
    repo, reviewed, head = _clean_merge(tmp_path)
    log = tmp_path / "gh.log"
    fake = tmp_path / "gh"
    body = f"Opus review on head `{reviewed}`.\n\n**PASS.**"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"log = {str(log)!r}\n"
        f"body = {body!r}\n"
        "args = sys.argv[1:]\n"
        "with open(log, 'a') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'api' and 'comments' in args[1]:\n"
        "    json.dump([{'body': body}], sys.stdout)\n"
        "    raise SystemExit(0)\n"
        "sys.stderr.write('unexpected')\n"
        "raise SystemExit(2)\n"
    )
    fake.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    completed = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "agents/lead/scripts/merge_delta.py"),
            "--repo-dir",
            str(repo),
            "--sha",
            head,
            "--base",
            "epic/team-chat",
            "--pr",
            "113",
            "--repo",
            "jholze/omnigent",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["kind"] == "clean"
    assert payload["last_reviewed"] == reviewed


def test_prompts_gate_reviews_on_ci_and_the_real_base() -> None:
    prompts = {
        "lead": _prompt(_ROOT / "agents/lead/config.yaml"),
        "tester": _prompt(_ROOT / "agents/lead/agents/tester/config.yaml"),
        "deepseek": _prompt(_ROOT / "agents/lead/agents/deepseek_review/config.yaml"),
        "gpt": _prompt(_ROOT / "agents/lead/agents/gpt_review/config.yaml"),
        "opus": _prompt(_ROOT / "agent-configs/omnigent-reviewer.yaml"),
        "ux": _prompt(_ROOT / "agent-configs/ux-reviewer/config.yaml"),
    }
    for name in ("tester", "deepseek", "gpt", "opus", "ux"):
        text = prompts[name]
        assert "agents/lead/scripts/ci_gate.py" in text
        assert "--once" in text
        assert "CI NOT GREEN" in text
        assert "baseRefName" in text
        assert "origin/main" not in text
        assert "MODE: DELTA" in text
    lead = prompts["lead"]
    assert "agents/lead/scripts/ci_gate.py" in lead
    assert "agents/lead/scripts/merge_delta.py" in lead
    for job in ci_gate.REQUIRED_CHECK_NAMES:
        assert job in lead
    assert "Never treat a failure as `clean`" in lead
    assert "git diff origin/main" not in lead
    assert "Never `git push` `main`" in lead
    assert "Only the Opus" in lead
    assert "Not run" in lead
    assert "MODE: SPEC" in lead
    fork = (_ROOT / "docs/fork-workflow.md").read_text()
    assert "required CI is green on the pinned" in fork
    assert "only merges the" in fork
