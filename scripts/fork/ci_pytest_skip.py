#!/usr/bin/env python3
"""Allowlist for skipping the fork pytest suite on a documentation PR.

Pytest (and the web vitest step) may be skipped only when ``git diff`` of the
pull request base against HEAD lists at least one path and every path is a
``*.md`` file outside ``tests/`` and outside ``specs/``. ``docs/**/*.md`` is
included. Anything else — config, YAML/JSON/TOML, specs, fixtures, workflow
files, paths under ``tests/``, an empty diff, a failed diff — keeps the suite.
``--no-renames`` counts both sides of a rename.

The command prints ``docs_only=false`` and exits 0 when it cannot decide, so
the CI job still runs the suite.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

_SHA = re.compile(r"[0-9a-fA-F]{40}")


def is_allowlisted_doc(path: str) -> bool:
    """Return True when one changed path may skip pytest.

    The path must end in ``.md`` (case-sensitive) and its first directory
    must not be ``tests`` or ``specs``. A root ``README.md`` qualifies.
    ``docs/superpowers/specs/...md`` qualifies because it lives under
    ``docs/``.
    """
    parts = _parts(path)
    if parts is None:
        return False
    if not parts[-1].endswith(".md"):
        return False
    if len(parts) > 1 and parts[0].casefold() in {"tests", "specs"}:
        return False
    return True


def pytest_suite_should_run(paths: list[str]) -> bool:
    """Return False only for a non-empty allowlisted path list."""
    if not paths:
        return True
    return not all(is_allowlisted_doc(path) for path in paths)


def changed_paths(base_sha: str) -> list[str]:
    """List paths changed between ``base_sha`` and HEAD, including renames.

    Fetches ``base_sha`` from origin when the shallow checkout does not have
    it. Raises ``RuntimeError`` when git cannot produce the list.
    """
    if not _SHA.fullmatch(base_sha):
        raise RuntimeError("base sha missing")
    if not _commit_exists(base_sha) and not _fetch(base_sha):
        raise RuntimeError("base commit unavailable")
    if not _commit_exists(base_sha):
        raise RuntimeError("base commit unavailable")
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base_sha, "HEAD"],
        check=False,
        capture_output=True,
    )
    if diff.returncode != 0:
        raise RuntimeError("git diff failed")
    return [part.decode("utf-8", "surrogateescape") for part in diff.stdout.split(b"\0") if part]


def decide_from_environment() -> tuple[bool, str, list[str]]:
    """Return ``(docs_only, reason, paths)`` for this Actions run."""
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return False, "not a pull request; pytest runs", []
    base = os.environ.get("BASE_SHA", "").strip()
    try:
        paths = changed_paths(base)
    except (RuntimeError, OSError) as exc:
        return False, f"{exc}; pytest runs", []
    if pytest_suite_should_run(paths):
        return False, f"{len(paths)} changed path(s); pytest runs", paths
    return True, f"all {len(paths)} changed path(s) are allowlisted markdown", paths


def classify_main() -> int:
    """Write ``docs_only`` and always exit 0."""
    try:
        docs_only, reason, paths = decide_from_environment()
    except Exception as exc:  # noqa: BLE001 - a classify bug must not skip pytest or fail the job
        docs_only, reason, paths = (
            False,
            f"classify failed ({exc.__class__.__name__}); pytest runs",
            [],
        )
    _log(docs_only, reason, paths)
    _write_output(docs_only)
    _write_summary(docs_only, reason, paths)
    return 0


def _commit_exists(sha: str) -> bool:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


def _fetch(sha: str) -> bool:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    fetched = subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", sha],
        check=False,
        capture_output=True,
        env=env,
    )
    return fetched.returncode == 0


def _parts(path: str) -> list[str] | None:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 4096
        or path.startswith(("/", "\\"))
        or any(char in path for char in "\n\r\t:\\")
    ):
        return None
    normalized = path
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return None
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return parts


def _log(docs_only: bool, reason: str, paths: list[str]) -> None:
    print(f"docs_only={'true' if docs_only else 'false'} ({reason})")
    escaped_newline = "\\" + "n"
    for path in paths:
        marker = "allow" if is_allowlisted_doc(path) else "keep"
        print(f"  {marker} {path.replace(chr(10), escaped_newline)}")


def _write_output(docs_only: bool) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"docs_only={'true' if docs_only else 'false'}\n")


def _write_summary(docs_only: bool, reason: str, paths: list[str]) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    kept = [path for path in paths if not is_allowlisted_doc(path)]
    shown = kept or paths
    lines = [
        "### Pytest allowlist",
        "",
        f"Pytest suite: {'skipped' if docs_only else 'runs'}",
        "",
        reason,
        "",
    ]
    for path in shown[:30]:
        visible = path.replace("`", "'")
        lines.append(f"- `{visible}`")
    if len(shown) > 30:
        lines.append(f"- … {len(shown) - 30} more")
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["classify"]:
        print("usage: ci_pytest_skip.py classify", file=sys.stderr)
        return 2
    return classify_main()


if __name__ == "__main__":
    sys.exit(main())
