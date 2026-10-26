#!/usr/bin/env python3
"""Allowlist for skipping the fork pytest suite on a documentation PR.

Pytest (and the web vitest step) may be skipped only when the pull request
file list is complete and every path is a ``*.md`` file outside ``tests/``
and outside ``specs/``. ``docs/**/*.md`` is included. Anything else — config,
YAML/JSON/TOML, specs, fixtures, workflow files, paths under ``tests/``, an
empty list, a truncated list — keeps the suite. A rename counts both paths.

The caller must not fail the CI job when this command cannot decide: it
prints ``docs_only=false`` and exits 0 so the suite still runs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

_MAX_LISTED_FILES = 3000
_MAX_PAGES = 40
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class FileListError(Exception):
    """The pull request file list could not be read completely."""


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


def paths_from_entries(entries: list[Mapping[str, Any]]) -> list[str]:
    """Return current and pre-rename paths from a pulls files payload."""
    paths: list[str] = []
    for entry in entries:
        filename = str(entry.get("filename") or "")
        paths.append(filename)
        previous = str(entry.get("previous_filename") or "")
        if previous and previous != filename:
            paths.append(previous)
    return paths


def is_docs_only(entries: list[Mapping[str, Any]], changed_files: int | None) -> bool:
    """Return True only for a complete allowlisted file list.

    ``changed_files`` is the pull request's reported count. A mismatch or a
    list at the API cap is treated as incomplete, so pytest runs.
    """
    if (
        changed_files is None
        or changed_files <= 0
        or changed_files >= _MAX_LISTED_FILES
        or len(entries) != changed_files
    ):
        return False
    paths = paths_from_entries(entries)
    if len(paths) < changed_files:
        return False
    return all(is_allowlisted_doc(path) for path in paths)


def fetch_pr_files(
    repo: str,
    number: str,
    token: str,
    opener: Any = None,
) -> list[dict[str, Any]]:
    """GET ``/pulls/{number}/files``, following ``Link: rel=next``."""
    open_url = opener or urllib.request.urlopen
    if not _REPO.fullmatch(repo):
        raise FileListError("unexpected repository")
    if not re.fullmatch(r"[0-9]+", number):
        raise FileListError("unexpected pull request number")
    if not token:
        raise FileListError("missing token")
    url: str | None = f"https://api.github.com/repos/{repo}/pulls/{number}/files?per_page=100"
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    while url:
        if url in seen or len(seen) >= _MAX_PAGES or not url.startswith("https://api.github.com/"):
            raise FileListError("files api pagination did not finish")
        seen.add(url)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "omnigent-fork-ci",
            },
        )
        try:
            with open_url(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                link = response.headers.get("Link")
        except urllib.error.HTTPError as exc:
            raise FileListError(f"files api status {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise FileListError("files api unreachable") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise FileListError("files api returned an unexpected list")
        entries.extend(payload)
        url = _next_link(link)
    return entries


def decide_from_environment() -> tuple[bool, str, list[str]]:
    """Return ``(docs_only, reason, paths)`` from the workflow environment."""
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return False, "not a pull request; pytest runs", []
    changed = _parse_count(os.environ.get("PR_CHANGED_FILES", ""))
    if changed is None:
        return False, "changed-file count missing; pytest runs", []
    entries = fetch_pr_files(
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("PR_NUMBER", ""),
        os.environ.get("GITHUB_TOKEN", ""),
    )
    paths = paths_from_entries(entries)
    docs_only = is_docs_only(entries, changed)
    if docs_only:
        return True, f"all {changed} changed file(s) are allowlisted markdown", paths
    return False, f"{changed} changed file(s); pytest runs", paths


def classify_main() -> int:
    """Write ``docs_only`` and always exit 0."""
    try:
        docs_only, reason, paths = decide_from_environment()
    except Exception as exc:  # noqa: BLE001 - a classify bug must not skip pytest or fail the job
        docs_only, reason, paths = False, f"file list unavailable ({exc.__class__.__name__})", []
    _log(docs_only, reason, paths)
    _write_output(docs_only)
    _write_summary(docs_only, reason, paths)
    return 0


def _parse_count(raw: str) -> int | None:
    stripped = raw.strip()
    if not re.fullmatch(r"[0-9]+", stripped):
        return None
    return int(stripped)


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


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    match = _NEXT_LINK.search(header)
    if match is None:
        return None
    return match.group(1)


def _log(docs_only: bool, reason: str, paths: list[str]) -> None:
    print(f"docs_only={'true' if docs_only else 'false'} ({reason})")
    newline = "\\n"
    for path in paths:
        marker = "allow" if is_allowlisted_doc(path) else "keep"
        print(f"  {marker} {path.replace(chr(10), newline)}")


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
