"""Allowlist cases for skipping fork pytest on documentation-only PRs.

Pytest runs unless every changed path is ``*.md`` outside ``tests/`` and
outside ``specs/``. The same decision skips vitest. Lint is not gated.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "fork" / "ci_pytest_skip.py"
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "fork-ci.yml"

_SPEC = importlib.util.spec_from_file_location("_ci_pytest_skip_under_test", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
ci_pytest_skip = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ci_pytest_skip
_SPEC.loader.exec_module(ci_pytest_skip)

# (changed paths, pytest suite runs)
_CASES: list[tuple[list[str], bool]] = [
    (["README.md"], False),
    (["docs/fork-workflow.md"], False),
    (["docs/a.md", "CHANGELOG.md"], False),
    (["docs/superpowers/specs/2026-06-23-web-ui-base-path-design.md"], False),
    (["AGENTS.md", "CONTRIBUTING.md", "SECURITY.md"], False),
    (["designs/REUSABLE_USER_AGENTS.md"], False),
    (["web/README.md"], False),
    (["feature-map/gitlab.md"], False),
    ([".github/pull_request_template.md"], False),
    (["specs/omnigent-chat/01-goals.md"], True),
    (["specs/README.md", "docs/fork-workflow.md"], True),
    (["tests/README.md"], True),
    (["tests/e2e_ui/visual/README.md"], True),
    (["Tests/README.md"], True),
    (["tests/scripts/test_review_mode.py"], True),
    (["docs/a.md", "tests/README.md"], True),
    (["docs/a.md", "omnigent/server/app.py"], True),
    (["pyproject.toml"], True),
    (["agents/lead/config.yaml"], True),
    (["docs/a.json"], True),
    (["docs/a.md", "docs/b.yaml"], True),
    (["docs/a.toml"], True),
    ([".github/workflows/fork-ci.yml"], True),
    (["tests/fixtures/antigravity/steps/checkpoint.json"], True),
    ([".github/workflows/fixtures/reopen-notice/closed.json"], True),
    (["docs/ux/scripts/contrast.py"], True),
    (["README.MD"], True),
    (["docs/Foo.markdown"], True),
    ([], True),
]


def _ids(case: tuple[list[str], bool]) -> str:
    paths, pytest_runs = case
    label = ",".join(paths) if paths else "(empty)"
    return ("run:" if pytest_runs else "skip:") + label


@pytest.mark.parametrize("paths,pytest_runs", _CASES, ids=[_ids(case) for case in _CASES])
def test_allowlist_cases(paths: list[str], pytest_runs: bool) -> None:
    assert ci_pytest_skip.pytest_suite_should_run(paths) is pytest_runs


def test_rename_paths_keep_or_skip_pytest() -> None:
    assert ci_pytest_skip.pytest_suite_should_run(["docs/a.md", "omnigent/a.py"]) is True
    assert ci_pytest_skip.pytest_suite_should_run(["docs/guide.md", "README.md"]) is False
    assert ci_pytest_skip.pytest_suite_should_run(["docs/a.md", "specs/a.md"]) is True


def test_parent_directory_escape_keeps_pytest() -> None:
    assert ci_pytest_skip.is_allowlisted_doc("tests/../README.md") is False
    assert ci_pytest_skip.is_allowlisted_doc("/README.md") is False


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(
        repo, "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", message
    )
    return _git(repo, "rev-parse", "HEAD")


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def test_git_diff_skips_docs_and_keeps_renames_from_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("hello\n")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("guide\n")
    _git(repo, "add", "README.md", "docs/guide.md")
    base = _commit(repo, "docs")
    (repo / "omnigent.py").write_text("print(1)\n")
    _git(repo, "add", "omnigent.py")
    _git(repo, "mv", "README.md", "docs/readme.md")
    _commit(repo, "code and rename")

    monkeypatch.chdir(repo)
    listed = ci_pytest_skip.changed_paths(base)
    assert "omnigent.py" in listed
    assert "README.md" in listed
    assert "docs/readme.md" in listed
    assert ci_pytest_skip.pytest_suite_should_run(listed) is True

    # A second repo whose only change is markdown outside tests/ and specs/.
    docs_repo = _init_repo(tmp_path / "docs-repo")
    (docs_repo / "README.md").write_text("v1\n")
    _git(docs_repo, "add", "README.md")
    docs_base = _commit(docs_repo, "base")
    (docs_repo / "README.md").write_text("v2\n")
    nested = docs_repo / "docs"
    nested.mkdir()
    (nested / "fork-workflow.md").write_text("doc\n")
    _git(docs_repo, "add", "README.md", "docs/fork-workflow.md")
    _commit(docs_repo, "docs only")
    monkeypatch.chdir(docs_repo)
    docs_paths = ci_pytest_skip.changed_paths(docs_base)
    assert docs_paths == ["README.md", "docs/fork-workflow.md"] or set(docs_paths) == {
        "README.md",
        "docs/fork-workflow.md",
    }
    assert ci_pytest_skip.pytest_suite_should_run(docs_paths) is False


def test_missing_base_keeps_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _commit(repo, "base")
    _git(repo, "remote", "add", "origin", str(tmp_path / "missing-origin"))
    output = tmp_path / "github_output"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("BASE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    assert ci_pytest_skip.classify_main() == 0
    assert output.read_text() == "docs_only=false\n"


def test_classify_without_a_pull_request_keeps_pytest(tmp_path: Path) -> None:
    output = tmp_path / "github_output"
    env = os.environ.copy()
    env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
    env["GITHUB_OUTPUT"] = str(output)
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "classify"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "docs_only=false\n"


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(_WORKFLOW.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _steps(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job["steps"] if "name" in step}


def test_workflow_skips_suite_steps_without_skipping_required_jobs() -> None:
    text = _WORKFLOW.read_text()
    assert "paths-ignore" not in text
    script = _SCRIPT.read_text()
    assert '"--name-only"' in script
    assert '"--no-renames"' in script
    workflow = _workflow()
    jobs = workflow["jobs"]
    pytest_job = jobs["pytest"]
    web_job = jobs["web"]
    lint_job = jobs["python-lint"]
    e2e_job = jobs["e2e-ui"]

    assert pytest_job["name"] == "Pytest (SQLite)"
    assert web_job["name"] == "Web (lint, typecheck, vitest, build)"
    assert lint_job["name"] == "Python lint (ruff, pyrefly)"
    assert "docs_only" not in str(lint_job["if"])
    assert pytest_job["outputs"]["docs_only"] == "${{ steps.classify.outputs.docs_only }}"
    assert workflow["permissions"] == {"contents": "read"}

    pytest_steps = _steps(pytest_job)
    web_steps = _steps(web_job)
    suite_if = pytest_steps["Run pytest (SQLite, not databricks)"]["if"]
    install_if = pytest_steps["Install dependencies"]["if"]
    vitest_if = web_steps["Unit tests (vitest run)"]["if"]
    assert "steps.classify.outputs.docs_only != 'true'" in suite_if
    assert "steps.classify.outputs.docs_only != 'true'" in install_if
    assert "steps.classify.outputs.docs_only != 'true'" in vitest_if
    assert "docs_only" not in str(web_steps["Lint (oxlint)"].get("if", ""))
    assert "docs_only" not in str(web_steps["Format check (prettier)"].get("if", ""))
    for job_steps in (pytest_steps, web_steps):
        step = job_steps["Classify changed files"]
        assert "ci_pytest_skip.py classify" in step["run"]
        assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
        assert "GITHUB_TOKEN" not in step["env"]
    assert "needs.pytest.outputs.docs_only != 'true'" in e2e_job["if"]
