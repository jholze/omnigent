#!/usr/bin/env python3
"""Allowlist for skipping the fork pytest suite on a documentation PR.

Pytest (and the web vitest step) may be skipped only when ``git diff`` of the
pull request base against HEAD lists at least one path and every path is a
``*.md`` file outside ``tests/`` and outside ``specs/``. ``docs/**/*.md`` is
included. Two extra gates keep the suite: a markdown file that any test reads,
and a ``docs/`` file that contains executable code (a shebang or a fenced
block in a language such as Python, bash, or TypeScript). Anything else —
config, YAML/JSON/TOML, specs, fixtures, workflow files, paths under
``tests/``, an empty diff, a failed diff — keeps the suite. ``--no-renames``
counts both sides of a rename.

The command prints ``docs_only=false`` and exits 0 when it cannot decide, so
the CI job still runs the suite. A test tree that cannot be scanned, or a
``docs/`` file that cannot be read, also keeps the suite.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

_SHA = re.compile(r"[0-9a-fA-F]{40}")
_FENCE = re.compile(r"(?m)^[ \t]{0,3}```+([^`\n]*)")
_QUOTED_MD = re.compile(r"""['"]([^'"\n]+\.md)['"]""")
_JS_READ = re.compile(r"readFileSync|\breadFile\b")
_MAX_DOC_BYTES = 2_000_000
_MAX_TEST_BYTES = 1_000_000
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".venv", "dist", "build"}
_EXECUTABLE_LANGS = frozenset(
    {
        "bash",
        "c",
        "cc",
        "console",
        "cpp",
        "cs",
        "csharp",
        "cxx",
        "fish",
        "go",
        "golang",
        "java",
        "javascript",
        "js",
        "jsx",
        "kotlin",
        "ksh",
        "kt",
        "lua",
        "make",
        "node",
        "nodejs",
        "perl",
        "php",
        "pl",
        "powershell",
        "ps1",
        "pwsh",
        "py",
        "py3",
        "python",
        "python3",
        "r",
        "rb",
        "rs",
        "ruby",
        "rust",
        "scala",
        "sh",
        "shell",
        "sql",
        "swift",
        "ts",
        "tsx",
        "typescript",
        "zsh",
    }
)
_JS_TEST_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


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


def contains_executable_code(text: str) -> bool:
    """Return True when ``text`` contains a shebang or an executable fence.

    The fence language is the first token, case-insensitive. ``xml``, ``json``,
    and unlabeled fences are not executable. A ``bash`` fence with a title
    attribute still counts.
    """
    if text.startswith("#!"):
        return True
    for match in _FENCE.finditer(text):
        info = match.group(1).strip()
        if not info:
            continue
        token = info.split()[0].split("{", 1)[0].split(":", 1)[0].strip("{}")
        if token.casefold() in _EXECUTABLE_LANGS:
            return True
    return False


def pytest_suite_should_run(paths: list[str], *, repo_root: Path | None = None) -> bool:
    """Return False only when every path may skip the suite.

    ``repo_root`` is the checkout those paths are relative to. It defaults to
    the working directory, which is the Actions workspace during classify.
    """
    root = Path.cwd() if repo_root is None else repo_root
    return _skip_block_reason(paths, root) is not None


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
    reason = _skip_block_reason(paths, Path.cwd())
    if reason is None:
        return True, f"all {len(paths)} changed path(s) are allowlisted markdown", paths
    if not paths or any(not is_allowlisted_doc(path) for path in paths):
        return False, f"{len(paths)} changed path(s); pytest runs", paths
    return False, f"{reason}; pytest runs", paths


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
    root = Path.cwd()
    _log(docs_only, reason, paths, root)
    _write_output(docs_only)
    _write_summary(docs_only, reason, paths, root)
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


@dataclass(frozen=True)
class _RepoPath:
    parts: tuple[str, ...]


@dataclass(frozen=True)
class _Str:
    value: str


@dataclass(frozen=True)
class _Int:
    value: int


@dataclass(frozen=True)
class _File:
    """The test module's own ``__file__``."""


@dataclass(frozen=True)
class _Parents:
    parts: tuple[str, ...]


_Atom = _RepoPath | _Str | _Int | _File | _Parents
_Value = frozenset[_Atom]


@dataclass(frozen=True)
class _MarkdownReads:
    paths: frozenset[str]
    globs: frozenset[tuple[tuple[str, ...], str, bool]]

    def covers(self, path: str) -> bool:
        if path in self.paths:
            return True
        candidate = PurePosixPath(path)
        for base, pattern, recursive in self.globs:
            relative = _relative_to_base(candidate, base)
            if relative is None:
                continue
            deep = recursive or "/" in pattern
            if deep and relative.match(pattern):
                return True
            if not deep and len(relative.parts) == 1 and relative.match(pattern):
                return True
        return False


def _relative_to_base(candidate: PurePosixPath, base: tuple[str, ...]) -> PurePosixPath | None:
    if not base:
        return candidate
    try:
        return candidate.relative_to(PurePosixPath(*base))
    except ValueError:
        return None


def _skip_block_reason(paths: list[str], repo_root: Path) -> str | None:
    """Return why the suite must run, or None when every path may skip."""
    if not paths:
        return "no changed paths"
    if any(not is_allowlisted_doc(path) for path in paths):
        return "outside the markdown allowlist"
    try:
        reads = _markdown_reads(repo_root)
    except OSError as exc:
        return f"could not scan tests ({exc.__class__.__name__})"
    blocks = [
        f"{path} {detail}"
        for path in paths
        if (detail := _allowlisted_block_detail(path, repo_root, reads)) is not None
    ]
    if blocks:
        return "; ".join(blocks)
    return None


def _allowlisted_block_detail(path: str, repo_root: Path, reads: _MarkdownReads) -> str | None:
    bits: list[str] = []
    if _docs_contain_executable_code(repo_root, path):
        bits.append("contains executable code")
    if reads.covers(path):
        bits.append("is read by a test")
    if not bits:
        return None
    return " and ".join(bits)


def _path_keeps_pytest(path: str, repo_root: Path) -> bool:
    if not is_allowlisted_doc(path):
        return True
    try:
        reads = _markdown_reads(repo_root)
    except OSError:
        return True
    return _allowlisted_block_detail(path, repo_root, reads) is not None


def _docs_contain_executable_code(repo_root: Path, path: str) -> bool:
    if not _is_docs_markdown(path):
        return False
    file_path = _contained_path(repo_root, path)
    if file_path is None:
        return True
    try:
        if file_path.stat().st_size > _MAX_DOC_BYTES:
            return True
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return contains_executable_code(text)


def _is_docs_markdown(path: str) -> bool:
    parts = _parts(path)
    return (
        parts is not None
        and len(parts) > 1
        and parts[0].casefold() == "docs"
        and parts[-1].endswith(".md")
    )


def _contained_path(repo_root: Path, path: str) -> Path | None:
    parts = _parts(path)
    if parts is None:
        return None
    try:
        root = repo_root.resolve()
        resolved = repo_root.joinpath(*parts).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return repo_root.joinpath(*parts)


def _markdown_reads(repo_root: Path) -> _MarkdownReads:
    return _cached_markdown_reads(str(repo_root.resolve()))


@lru_cache(maxsize=8)
def _cached_markdown_reads(root: str) -> _MarkdownReads:
    return _scan_markdown_reads(Path(root))


def _scan_markdown_reads(root: Path) -> _MarkdownReads:
    paths: set[str] = set()
    globs: set[tuple[tuple[str, ...], str, bool]] = set()
    for file_path in _python_test_files(root):
        _scan_python_test(file_path, root, paths, globs)
    for file_path in _js_test_files(root):
        _scan_js_test(file_path, paths)
    return _MarkdownReads(paths=frozenset(paths), globs=frozenset(globs))


def _python_test_files(root: Path) -> list[Path]:
    return _walk_files(root / "tests", lambda path: path.suffix == ".py")


def _js_test_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for base in (root / "tests", root / "web"):
        found.extend(_walk_files(base, lambda path: path.name.endswith(_JS_TEST_SUFFIXES)))
    return found


def _walk_files(base: Path, include: object) -> list[Path]:
    predicate = include
    if not base.is_dir() or not callable(predicate):
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if predicate(path):
                found.append(path)
    return found


def _scan_python_test(
    file_path: Path,
    root: Path,
    paths: set[str],
    globs: set[tuple[tuple[str, ...], str, bool]],
) -> None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(file_path) from exc
    if ".md" not in text and "glob(" not in text:
        return
    file_parts = _file_parts(file_path, root)
    if file_parts is None:
        return
    if len(text) > _MAX_TEST_BYTES:
        _record_quoted_markdown(text, paths)
        return
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _record_quoted_markdown(text, paths)
        return
    scanner = _TestReadScanner(
        file_parts=file_parts,
        source_has_read=(".read_text" in text or ".read_bytes" in text or "open(" in text),
    )
    scanner.visit(tree)
    paths.update(scanner.paths)
    globs.update(scanner.globs)


def _scan_js_test(file_path: Path, paths: set[str]) -> None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OSError(file_path) from exc
    if ".md" not in text or _JS_READ.search(text) is None:
        return
    _record_quoted_markdown(text, paths)


def _record_quoted_markdown(text: str, paths: set[str]) -> None:
    for match in _QUOTED_MD.finditer(text):
        parts = _parts(match.group(1))
        if parts is not None and parts[-1].endswith(".md"):
            paths.add("/".join(parts))


def _file_parts(file_path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        relative = file_path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        return None
    return relative.parts


class _TestReadScanner(ast.NodeVisitor):
    """Collect repo markdown paths a test file reads.

    Path joins that start at ``Path(__file__)`` or a relative string are
    resolved. A ``glob``/``rglob`` of ``*.md`` counts only when the same file
    also reads file contents, so a name-only listing does not force pytest.
    Reads of ``tmp_path`` and other unresolved fixtures are ignored.
    """

    def __init__(self, file_parts: tuple[str, ...], source_has_read: bool) -> None:
        self.file_parts = file_parts
        self.source_has_read = source_has_read
        self.env: dict[str, _Value] = {}
        self.paths: set[str] = set()
        self.globs: set[tuple[tuple[str, ...], str, bool]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self._eval(decorator)
        outer = dict(self.env)
        for stmt in node.body:
            self.visit(stmt)
        self.env = outer

    def visit_Assign(self, node: ast.Assign) -> None:
        value = self._eval(node.value)
        for target in node.targets:
            self._bind(target, value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._eval(node.annotation)
        if node.value is not None:
            self._bind(node.target, self._eval(node.value))

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.expr):
            self._eval(node)
            return
        super().generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self._eval(decorator)
        outer = dict(self.env)
        for name, value in _parametrize_bindings(node).items():
            self.env[name] = value
        for stmt in node.body:
            self.visit(stmt)
        self.env = outer

    def _bind(self, target: ast.AST, value: _Value) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
            return
        if isinstance(target, ast.Tuple | ast.List):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    self.env[elt.id] = frozenset()

    def _eval(self, node: ast.AST | None) -> _Value:
        if node is None:
            return frozenset()
        if isinstance(node, ast.Constant):
            return _constant(node.value)
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return frozenset({_File()})
            return self.env.get(node.id, frozenset())
        if isinstance(node, ast.Attribute):
            return self._eval_attr(node)
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.JoinedStr):
            return self._eval_joined(node)
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return self._eval_sequence(node.elts)
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self._eval_comp(node)
        if isinstance(node, ast.IfExp):
            self._eval(node.test)
            return self._eval(node.body) | self._eval(node.orelse)
        if isinstance(node, ast.NamedExpr):
            value = self._eval(node.value)
            self._bind(node.target, value)
            return value
        if isinstance(node, ast.Starred):
            return self._eval(node.value)
        for child in ast.iter_child_nodes(node):
            self._eval(child)
        return frozenset()

    def _eval_attr(self, node: ast.Attribute) -> _Value:
        receiver = self._eval(node.value)
        if node.attr == "parent":
            return _map_parents(receiver, 0)
        if node.attr == "parents":
            return frozenset(
                _Parents(item.parts) for item in receiver if isinstance(item, _RepoPath)
            )
        return frozenset()

    def _eval_subscript(self, node: ast.Subscript) -> _Value:
        value = self._eval(node.value)
        indexes = [item.value for item in self._eval(node.slice) if isinstance(item, _Int)]
        found: set[_Atom] = set()
        for item in value:
            if not isinstance(item, _Parents):
                continue
            for index in indexes:
                parent = _parent_at(item.parts, index)
                if parent is not None:
                    found.add(parent)
        return frozenset(found)

    def _eval_binop(self, node: ast.BinOp) -> _Value:
        left = self._eval(node.left)
        right = self._eval(node.right)
        if isinstance(node.op, ast.Div):
            return _join_paths(self._paths_from(left), self._paths_from(right))
        return frozenset()

    def _eval_call(self, node: ast.Call) -> _Value:
        func = node.func
        if _is_path_ctor(func):
            return self._eval_path_ctor(node)
        if _is_open(func):
            if node.args:
                self._record_paths(self._eval(node.args[0]))
            for arg in node.args[1:]:
                self._eval(arg)
            self._eval_keywords(node)
            return frozenset()
        if isinstance(func, ast.Attribute):
            return self._eval_method(node, func)
        self._eval(func)
        for arg in node.args:
            self._eval(arg)
        self._eval_keywords(node)
        return frozenset()

    def _eval_method(self, node: ast.Call, func: ast.Attribute) -> _Value:
        receiver = self._eval(func.value)
        method = func.attr
        if method in {"resolve", "absolute", "expanduser"} and not node.args:
            self._eval_keywords(node)
            return receiver
        if method in {"glob", "rglob"} and node.args:
            pattern = self._eval(node.args[0])
            for arg in node.args[1:]:
                self._eval(arg)
            self._eval_keywords(node)
            self._record_globs(receiver, pattern, recursive=method == "rglob")
            return frozenset()
        if method in {"read_text", "read_bytes"}:
            self._record_paths(receiver)
            for arg in node.args:
                self._eval(arg)
            self._eval_keywords(node)
            return frozenset()
        for arg in node.args:
            self._eval(arg)
        self._eval_keywords(node)
        return frozenset()

    def _eval_path_ctor(self, node: ast.Call) -> _Value:
        self._eval_keywords(node)
        if not node.args:
            return frozenset({_RepoPath(())})
        acc: _Value = frozenset({_RepoPath(())})
        for arg in node.args:
            acc = _join_paths(acc, self._paths_from(self._eval(arg)))
        return acc

    def _eval_keywords(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.value is not None:
                self._eval(keyword.value)

    def _eval_joined(self, node: ast.JoinedStr) -> _Value:
        pieces: list[set[str]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append({value.value})
                continue
            if isinstance(value, ast.FormattedValue):
                evaluated = self._eval(value.value)
                strings = {item.value for item in evaluated if isinstance(item, _Str)}
                if not strings:
                    return frozenset()
                pieces.append(strings)
                continue
            return frozenset()
        acc = {""}
        for group in pieces:
            acc = {prefix + piece for prefix in acc for piece in group}
            if len(acc) > 32:
                return frozenset()
        return frozenset(_Str(text) for text in acc)

    def _eval_sequence(self, elts: list[ast.expr]) -> _Value:
        merged: set[_Atom] = set()
        for elt in elts:
            merged.update(self._eval(elt))
            if len(merged) > 32:
                return frozenset(merged)
        return frozenset(merged)

    def _eval_comp(self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp) -> _Value:
        outer = dict(self.env)
        for generator in node.generators:
            value = self._eval(generator.iter)
            useful = frozenset(item for item in value if isinstance(item, _RepoPath | _Str))
            if isinstance(generator.target, ast.Name) and useful:
                self.env[generator.target.id] = useful
            for test in generator.ifs:
                self._eval(test)
        result = self._eval(node.elt)
        self.env = outer
        return result

    def _paths_from(self, value: _Value) -> _Value:
        found: set[_Atom] = set()
        for item in value:
            if isinstance(item, _RepoPath):
                found.add(item)
            elif isinstance(item, _File):
                found.add(_RepoPath(self.file_parts))
            elif isinstance(item, _Str):
                parts = _parts(item.value)
                if parts is not None:
                    found.add(_RepoPath(tuple(parts)))
        return frozenset(found)

    def _record_paths(self, value: _Value) -> None:
        for item in self._paths_from(value):
            if isinstance(item, _RepoPath) and item.parts:
                self.paths.add("/".join(item.parts))

    def _record_globs(self, receiver: _Value, pattern_value: _Value, *, recursive: bool) -> None:
        if not self.source_has_read:
            return
        patterns = [
            item.value for item in pattern_value if isinstance(item, _Str) and ".md" in item.value
        ]
        bases = [item for item in self._paths_from(receiver) if isinstance(item, _RepoPath)]
        for base in bases:
            for pattern in patterns:
                self.globs.add((base.parts, pattern, recursive))


def _constant(value: object) -> _Value:
    if isinstance(value, str):
        return frozenset({_Str(value)})
    if isinstance(value, int) and not isinstance(value, bool):
        return frozenset({_Int(value)})
    return frozenset()


def _map_parents(value: _Value, index: int) -> _Value:
    found: set[_Atom] = set()
    for item in value:
        if isinstance(item, _RepoPath):
            parent = _parent_at(item.parts, index)
            if parent is not None:
                found.add(parent)
    return frozenset(found)


def _parent_at(parts: tuple[str, ...], index: int) -> _RepoPath | None:
    """Return ``Path.parents[index]`` when that parent is still inside the repo."""
    if index < 0:
        return None
    drop = index + 1
    if drop > len(parts):
        return None
    return _RepoPath(parts[: len(parts) - drop])


def _join_paths(left: _Value, right: _Value) -> _Value:
    joined: set[_Atom] = set()
    bases = [item for item in left if isinstance(item, _RepoPath)]
    segments = [item for item in right if isinstance(item, _RepoPath)]
    for base in bases:
        for segment in segments:
            joined.add(_RepoPath(base.parts + segment.parts))
            if len(joined) > 32:
                return frozenset(joined)
    return frozenset(joined)


def _is_path_ctor(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id in {"Path", "PurePath", "PurePosixPath"}
    return isinstance(func, ast.Attribute) and func.attr in {"Path", "PurePath", "PurePosixPath"}


def _is_open(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "open"
    return isinstance(func, ast.Attribute) and func.attr == "open"


def _parametrize_bindings(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, _Value]:
    bindings: dict[str, set[_Atom]] = {}
    for decorator in fn.decorator_list:
        if not isinstance(decorator, ast.Call) or not _is_parametrize(decorator.func):
            continue
        if len(decorator.args) < 2:
            continue
        names = _literal_strings(decorator.args[0])
        rows = _literal_rows(decorator.args[1])
        if not names or rows is None or len(rows) > 32:
            continue
        _bind_parametrize_rows(bindings, names, rows)
    return {name: frozenset(atoms) for name, atoms in bindings.items()}


def _bind_parametrize_rows(
    bindings: dict[str, set[_Atom]], names: list[str], rows: list[list[str]]
) -> None:
    if len(names) == 1:
        bindings.setdefault(names[0], set()).update(_Str(row[0]) for row in rows if len(row) == 1)
        return
    for row in rows:
        if len(row) != len(names):
            continue
        for name, value in zip(names, row, strict=True):
            bindings.setdefault(name, set()).add(_Str(value))


def _is_parametrize(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "parametrize"
    return isinstance(func, ast.Attribute) and func.attr == "parametrize"


def _literal_strings(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.List | ast.Tuple):
        values: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                return None
        return values
    return None


def _literal_rows(node: ast.AST) -> list[list[str]] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    rows: list[list[str]] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            rows.append([elt.value])
            continue
        nested = _literal_strings(elt)
        if nested is None:
            return None
        rows.append(nested)
    return rows


def _log(docs_only: bool, reason: str, paths: list[str], repo_root: Path) -> None:
    print(f"docs_only={'true' if docs_only else 'false'} ({reason})")
    escaped_newline = "\\" + "n"
    for path in paths:
        marker = "keep" if _path_keeps_pytest(path, repo_root) else "allow"
        print(f"  {marker} {path.replace(chr(10), escaped_newline)}")


def _write_output(docs_only: bool) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"docs_only={'true' if docs_only else 'false'}\n")


def _write_summary(docs_only: bool, reason: str, paths: list[str], repo_root: Path) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    kept = [path for path in paths if _path_keeps_pytest(path, repo_root)]
    shown = kept or paths
    lines = [
        "### Pytest allowlist",
        "",
        f"Pytest suite: {'skipped' if docs_only else 'runs'}",
        "",
        reason.replace("`", "'"),
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
