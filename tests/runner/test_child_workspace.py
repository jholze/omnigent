"""Security tests for the child-workspace containment helper (issue #205).

A child session may run in its OWN workspace only when, after full symlink
resolution, it is the hub's workspace, a directory inside it, or a git worktree
of the SAME repository. These tests pin each acceptance case and each rejection
(``..`` escape, symlink escape, a different repo's worktree, a missing path, a
non-directory, a foreign host's path).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from omnigent.host.git_worktree import create_worktree, git_common_dir, same_repository
from omnigent.runner.child_workspace import resolve_child_workspace_within_reach


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _init_repo(path: Path) -> Path:
    """Create a one-commit git repo at *path* and return it."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "dev@example.com", cwd=path)
    _git("config", "user.name", "Example Dev", cwd=path)
    (path / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "init", cwd=path)
    return path


def test_accepts_the_base_itself(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    resolved = resolve_child_workspace_within_reach(str(base), str(base))
    assert resolved == os.path.realpath(base)


def test_accepts_a_directory_inside_the_base(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    inner = base / "pkg" / "sub"
    inner.mkdir(parents=True)
    resolved = resolve_child_workspace_within_reach(str(inner), str(base))
    assert resolved == os.path.realpath(inner)


def test_accepts_a_worktree_of_the_same_repository(tmp_path: Path) -> None:
    base = _init_repo(tmp_path / "repo")
    created = create_worktree(repo_path=str(base), branch_name="feat/x", base_branch="main")
    # The worktree lives OUTSIDE the base dir (sibling ``repo-worktrees/``) but
    # is the same repository, so it is reachable.
    assert not os.path.realpath(created.worktree_path).startswith(os.path.realpath(base) + os.sep)
    resolved = resolve_child_workspace_within_reach(created.worktree_path, str(base))
    assert resolved == os.path.realpath(created.worktree_path)


def test_rejects_a_dotdot_escape(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    sibling = tmp_path / "secret"
    sibling.mkdir()
    escape = base / ".." / "secret"
    assert resolve_child_workspace_within_reach(str(escape), str(base)) is None


def test_rejects_a_symlink_that_points_outside(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "link"
    link.symlink_to(outside, target_is_directory=True)
    # The link is lexically inside base, but realpath lands outside it.
    assert resolve_child_workspace_within_reach(str(link), str(base)) is None


def test_rejects_a_worktree_of_a_different_repository(tmp_path: Path) -> None:
    base = _init_repo(tmp_path / "repo")
    other = _init_repo(tmp_path / "other")
    created = create_worktree(repo_path=str(other), branch_name="feat/y", base_branch="main")
    assert resolve_child_workspace_within_reach(created.worktree_path, str(base)) is None


def test_rejects_a_nonexistent_path(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    assert resolve_child_workspace_within_reach(str(base / "nope"), str(base)) is None


def test_rejects_a_non_directory(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    file_path = base / "file.txt"
    file_path.write_text("x")
    assert resolve_child_workspace_within_reach(str(file_path), str(base)) is None


@pytest.mark.parametrize("candidate,base", [("", "/repo"), ("/repo", ""), ("  ", "/repo")])
def test_rejects_empty_inputs(candidate: str, base: str) -> None:
    assert resolve_child_workspace_within_reach(candidate, base) is None


def test_same_repository_true_for_worktree_and_main(tmp_path: Path) -> None:
    base = _init_repo(tmp_path / "repo")
    created = create_worktree(repo_path=str(base), branch_name="feat/z", base_branch="main")
    assert same_repository(created.worktree_path, str(base))


def test_same_repository_false_across_repos(tmp_path: Path) -> None:
    a = _init_repo(tmp_path / "a")
    b = _init_repo(tmp_path / "b")
    assert not same_repository(str(a), str(b))


def test_git_common_dir_none_for_non_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_common_dir(str(plain)) is None
    assert git_common_dir(str(tmp_path / "missing")) is None
