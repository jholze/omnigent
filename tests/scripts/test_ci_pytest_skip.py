"""Allowlist cases for skipping fork pytest on documentation-only PRs.

Pytest runs unless every changed path is ``*.md`` outside ``tests/`` and
outside ``specs/``. The same decision skips vitest. Lint is not gated.
"""

from __future__ import annotations

import importlib.util
import json
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


def _docs_only(entries: list[dict[str, str]], changed_files: int | None) -> bool:
    return ci_pytest_skip.is_docs_only(entries, changed_files)


@pytest.mark.parametrize("paths,pytest_runs", _CASES, ids=[_ids(case) for case in _CASES])
def test_allowlist_cases(paths: list[str], pytest_runs: bool) -> None:
    entries = [{"filename": path} for path in paths]
    assert _docs_only(entries, len(entries)) is (not pytest_runs)


def test_rename_from_python_keeps_pytest() -> None:
    entries = [
        {"filename": "docs/a.md", "previous_filename": "omnigent/a.py", "status": "renamed"}
    ]
    assert _docs_only(entries, 1) is False


def test_rename_between_allowlisted_markdown_skips_pytest() -> None:
    entries = [
        {"filename": "docs/guide.md", "previous_filename": "README.md", "status": "renamed"}
    ]
    assert _docs_only(entries, 1) is True


def test_rename_out_of_specs_keeps_pytest() -> None:
    entries = [{"filename": "docs/a.md", "previous_filename": "specs/a.md", "status": "renamed"}]
    assert _docs_only(entries, 1) is False


def test_incomplete_or_capped_list_keeps_pytest() -> None:
    readme = [{"filename": "README.md"}]
    assert _docs_only(readme, 2) is False
    assert _docs_only(readme, None) is False
    capped = [{"filename": "README.md"} for _ in range(3000)]
    assert _docs_only(capped, 3000) is False


def test_parent_directory_escape_keeps_pytest() -> None:
    assert ci_pytest_skip.is_allowlisted_doc("tests/../README.md") is False
    assert ci_pytest_skip.is_allowlisted_doc("/README.md") is False


class _Response:
    def __init__(self, payload: list[dict[str, str]], link: str | None = None) -> None:
        self._body = json.dumps(payload).encode()
        self.headers = {"Link": link} if link else {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def test_fetch_follows_next_link_and_stops_on_github_host() -> None:
    pages = {
        "https://api.github.com/repos/jholze/omnigent/pulls/7/files?per_page=100": _Response(
            [{"filename": "README.md"}],
            '<https://api.github.com/repos/jholze/omnigent/pulls/7/files?page=2>; rel="next"',
        ),
        "https://api.github.com/repos/jholze/omnigent/pulls/7/files?page=2": _Response(
            [{"filename": "docs/a.md", "previous_filename": "docs/b.md"}]
        ),
    }
    seen: list[str] = []

    def opener(request: Any, timeout: int) -> _Response:
        assert timeout == 30
        assert request.get_header("Authorization") == "Bearer test-token"
        seen.append(request.full_url)
        return pages[request.full_url]

    entries = ci_pytest_skip.fetch_pr_files("jholze/omnigent", "7", "test-token", opener)
    assert [entry["filename"] for entry in entries] == ["README.md", "docs/a.md"]
    assert seen == list(pages)
    assert _docs_only(entries, 2) is True


def test_fetch_rejects_non_github_next_link() -> None:
    def opener(_request: Any, timeout: int) -> _Response:
        assert timeout == 30
        return _Response(
            [{"filename": "README.md"}],
            '<https://example.invalid/files>; rel="next"',
        )

    with pytest.raises(ci_pytest_skip.FileListError):
        ci_pytest_skip.fetch_pr_files("jholze/omnigent", "7", "test-token", opener)


def test_classify_without_a_pull_request_keeps_pytest(tmp_path: Path) -> None:
    output = tmp_path / "github_output"
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
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


def test_classify_error_exits_zero_and_keeps_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github_output"

    def boom(*_args: object, **_kwargs: object) -> list[dict[str, str]]:
        raise ci_pytest_skip.FileListError("nope")

    monkeypatch.setattr(ci_pytest_skip, "fetch_pr_files", boom)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jholze/omnigent")
    monkeypatch.setenv("PR_NUMBER", "7")
    monkeypatch.setenv("PR_CHANGED_FILES", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert ci_pytest_skip.classify_main() == 0
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
    assert "ci_pytest_skip.py classify" in pytest_steps["Classify changed files"]["run"]
    assert "ci_pytest_skip.py classify" in web_steps["Classify changed files"]["run"]
    assert "needs.pytest.outputs.docs_only != 'true'" in e2e_job["if"]
    assert workflow["permissions"]["pull-requests"] == "read"
    assert workflow["permissions"]["contents"] == "read"
