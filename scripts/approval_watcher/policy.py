"""Fail-closed allow-list for ``session.awaiting_decision`` cards.

Pure functions, no I/O beyond path normalisation, so tests can feed
command strings directly. A card is approved only when a command can be
extracted, every program in it is on the allow-list, and every argument
passes the path, secret, and network checks. Anything else is reported.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_FIXED_LOCAL_ROOTS = ("/tmp", "/private/tmp")
_FIXED_LOCAL_FILES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

# A program named by absolute path is trusted only from these directories.
_TRUSTED_BIN_DIRS = frozenset(
    {"/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/sbin", "/usr/sbin"}
)

# Codex command approvals carry the command only inside the message.
_CODEX_COMMAND_PHASE = "codex_command_approval"
_CODEX_COMMAND_POLICY = "codex_native_command_approval"
_CODEX_MESSAGE_RE = re.compile(r"\A\s*Codex wants to run \*\*(?P<command>.+)\*\*\s*\Z", re.DOTALL)

_SEGMENT_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "|&", ";;", "(", ")", "{", "}"})
_REDIRECT_RE = re.compile(r"^[<>&|]*>[<>&|]*$")
_INPUT_REDIRECTS = frozenset({"<", "<<", "<<<"})
_PUNCTUATION_CHARS = frozenset("();<>|&")
_MAX_SHELL_DEPTH = 3

# Programs whose arguments only need the generic path/secret/network checks.
_PLAIN_TOOLS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "echo",
        "pwd",
        "test",
        "[",
        "diff",
        "true",
        "false",
        "basename",
        "dirname",
        "realpath",
        "ruff",
    }
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_SHELL_FLAG_LETTERS = frozenset("lcexu")
_SHELL_LONG_FLAGS = frozenset({"--login", "--noprofile", "--norc"})
_INTERPRETERS = frozenset({"python", "python3", "node", "deno", "bun", "ruby", "perl", "php"})
# Interpreter options that run code given on the command line.
_INLINE_CODE_LETTERS = frozenset("ceEpr")
_INLINE_CODE_LONG_FLAGS = frozenset({"--eval", "--print", "--command"})
_VERSIONED_PROGRAM_RE = re.compile(r"^(python|node|ruby|perl|php)[\d.]+$")
_NETWORK_TOOLS = frozenset({"curl", "wget"})

_FIND_ACTIONS_DENIED = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)
_SAFE_ENV_NAME_RE = re.compile(
    r"^(?:CI|TERM|NO_COLOR|FORCE_COLOR|COLUMNS|LINES|LANG|LC_[A-Z]+|TZ)$"
)

# Read-only git inspection. Every git write (add, commit, checkout, ...) reports.
_GIT_ALLOWED_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "cat-file",
        "blame",
        "grep",
        "describe",
        "shortlog",
        "merge-base",
        "name-rev",
        "version",
        "show-ref",
        "for-each-ref",
        "check-ignore",
        "diff-tree",
        "diff-files",
        "diff-index",
        "range-diff",
        "whatchanged",
        "count-objects",
    }
)
_GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset({"-C"})
_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P", "--bare", "--literal-pathspecs"})
_GIT_GLOBAL_PATH_PREFIXES = ("--git-dir=", "--work-tree=")

_UV_FLAGS = frozenset({"-q", "--quiet", "--frozen", "--locked", "--offline", "--no-sync"})
_UV_OPTIONS_WITH_VALUE = frozenset({"--project", "--directory", "--package"})
_NODE_PM_FLAGS = frozenset({"-r", "--recursive", "-s", "--silent", "--if-present"})
_NODE_PM_OPTIONS_WITH_VALUE = frozenset({"--filter", "-F", "-C", "--dir", "--prefix"})
_NODE_PM_DIRECTORY_OPTIONS = frozenset({"-C", "--dir", "--prefix"})
_PYTEST_PATH_OPTIONS = frozenset({"-c", "--config-file", "--rootdir", "--confcutdir"})
_PYTEST_LONG_OPTIONS_WITH_VALUE = frozenset(
    {
        "--config-file",
        "--rootdir",
        "--confcutdir",
        "--override-ini",
        "--maxfail",
        "--tb",
        "--durations",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--junitxml",
        "--junit-xml",
        "--log-file",
        "--import-mode",
        "--capture",
        "--color",
        "--log-level",
        "--log-cli-level",
        "--durations-min",
    }
)
# Long flags without a value. Any other long option is reported.
_PYTEST_LONG_FLAGS = frozenset(
    {
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--nf",
        "--new-first",
        "--sw",
        "--stepwise",
        "--co",
        "--collect-only",
        "--exitfirst",
        "--verbose",
        "--quiet",
        "--no-header",
        "--no-summary",
        "--showlocals",
        "--strict-markers",
        "--strict-config",
        "--disable-warnings",
        "--setup-show",
        "--runxfail",
        "--doctest-modules",
    }
)
# pytest writes these report files; the target must be /tmp or /dev/null.
_PYTEST_OUTPUT_FILE_OPTIONS = frozenset({"--junitxml", "--junit-xml", "--log-file"})
# Short options: no-value flags may cluster (-qx); value options may attach (-ra, -pno:x).
_PYTEST_SHORT_FLAGS = frozenset("qvxslh")
_PYTEST_SHORT_WITH_VALUE = frozenset("ckmnoprW")
# ini keys that move imports, collection, or config outside the command line.
_PYTEST_RISKY_INI_KEYS = frozenset(
    {
        "pythonpath",
        "testpaths",
        "rootdir",
        "confcutdir",
        "addopts",
        "required_plugins",
        "cache_dir",
        "log_file",
    }
)
# Programs that run project code or config from the cwd; a cd out of the
# workspace reports them. git counts: a foreign repo's config runs programs.
_PROJECT_TOOLS = frozenset({"npm", "pnpm", "uv", "just", "pytest", "ruff", "git"})
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Shell parameter expansion: ${...}, $NAME, $1, $?, $'...', $"...", $[...].
_EXPANSION_RE = re.compile(r"\$(?![\s|)]|$)")
_NODE_PM_SUBCOMMANDS = frozenset({"test", "t", "run", "run-script", "ls", "list", "why"})
_JUST_FLAGS = frozenset({"-l", "--list", "-n", "--dry-run", "--summary"})

_TIMEOUT_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

_SECRET_TOKEN_RE = re.compile(
    r"(?i)"
    r"(?:^|[/\\])\.ssh(?:$|[/\\])"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)"
    r"|(?:^|[/\\=])\.env(?:rc)?(?:\.[\w.-]+)?$"
    r"|(?:^|[/\\=])\.env(?:rc)?[/\\]"
    r"|credential"
    r"|\.netrc|\.npmrc|\.pypirc|\.git-credentials"
    r"|(?:^|[/\\])\.(?:aws|gnupg|kube|docker)(?:$|[/\\])"
    r"|(?:^|[/\\])\.omnigent(?:$|[/\\])"
    r"|secret|token|passw(?:or)?d|keychain"
    r"|\.(?:pem|p12|pfx|key|keystore|jks)$"
)
_DESTRUCTIVE_SQL_RE = re.compile(
    r"(?i)\b(?:drop|truncate)\s+(?:table|database|schema|index)\b|\bdelete\s+from\b"
)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"]*")
_SCP_REMOTE_RE = re.compile(r"^[\w.+-]+@[\w.-]+:")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True)
class Decision:
    """Verdict for one card.

    :param approve: True only when every allow-list rule passed.
    :param reason: Short, secret-free explanation, e.g. ``"git push"``.
    :param command: Extracted command text, or ``None`` when absent.
    """

    approve: bool
    reason: str
    command: str | None = None


def extract_command(data: dict[str, Any]) -> str | None:
    """Return the inspectable shell command on a card, or ``None``.

    Sources, in order: an explicit ``command`` field on ``data`` or
    ``data.request``, then the Codex command-approval message. Other
    cards (Claude ``wants to call **Bash**``, forms, plans) carry no
    command in the webhook payload and yield ``None``.
    """
    request = data.get("request") if isinstance(data.get("request"), dict) else {}
    for source in (data, request):
        explicit = source.get("command")
        if isinstance(explicit, str) and explicit.strip():
            return explicit
        if isinstance(explicit, list) and explicit and all(isinstance(p, str) for p in explicit):
            return shlex.join(explicit)
    phase = request.get("phase") or data.get("phase")
    policy_name = request.get("policy_name") or data.get("policy_name")
    if phase != _CODEX_COMMAND_PHASE and policy_name != _CODEX_COMMAND_POLICY:
        return None
    message = request.get("message") or data.get("message")
    if not isinstance(message, str):
        return None
    match = _CODEX_MESSAGE_RE.match(message)
    return match.group("command") if match else None


def _schema_has_fields(schema: Any) -> bool:
    if not schema:
        return False
    if not isinstance(schema, dict):
        return True
    properties = schema.get("properties")
    return bool(properties) or bool(schema.get("required"))


def evaluate_card(data: dict[str, Any], *, workspace_roots: Sequence[str]) -> Decision:
    """Decide one ``session.awaiting_decision`` ``data`` object."""
    if not isinstance(data, dict):
        return Decision(False, "payload data is not an object")
    request = data.get("request") if isinstance(data.get("request"), dict) else {}
    schema = data.get("requestedSchema", request.get("requestedSchema"))
    if _schema_has_fields(schema):
        return Decision(False, "card asks for form fields; not a command card")
    command = extract_command(data)
    if command is None:
        return Decision(False, "no inspectable command on card")
    # Check every cwd the card carries (data and request); null means absent.
    # Empty or relative values would resolve against the watcher's own cwd.
    cwds = [source.get("cwd") for source in (data, request) if source.get("cwd") is not None]
    for cwd in cwds:
        if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd):
            return Decision(False, "card cwd outside workspace", command)
        if not _is_under_roots(cwd, workspace_roots):
            return Decision(False, "card cwd outside workspace", command)
    # /tmp is fine for ls/cat, but project tools there would run foreign code.
    cwd_outside_workspace = any(
        not _is_under_roots(cwd, workspace_roots, include_fixed_roots=False) for cwd in cwds
    )
    decision = evaluate_command(
        command,
        workspace_roots=workspace_roots,
        cwd_outside_workspace=cwd_outside_workspace,
        cwds=cwds,
    )
    return Decision(decision.approve, decision.reason, command)


@dataclass
class _WalkState:
    """State shared by every segment of one command, nested ``-c`` scripts included.

    :param cwd_outside_workspace: True once a ``cd`` left the workspace roots
        (``/tmp`` counts as outside here), so later project tools would run
        a foreign project's scripts or config.
    """

    cwd_outside_workspace: bool = False
    # Resolved candidates for the current directory: the card cwd, else every
    # workspace root (the agent's cwd is not in the payload), updated by cd.
    base_dirs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Scope:
    """What a project-directory check needs: the roots and the current base dirs."""

    workspace_roots: Sequence[str]
    base_dirs: Sequence[str]


def evaluate_command(
    command: str,
    *,
    workspace_roots: Sequence[str],
    cwd_outside_workspace: bool = False,
    cwds: Sequence[str] = (),
) -> Decision:
    """Apply the allow-list to one shell command string.

    :param cwd_outside_workspace: True when the command starts in a directory
        outside the workspace roots (for example a card ``cwd`` under ``/tmp``).
    :param cwds: Absolute directories the command may start in (the card's
        cwd values). Relative project paths are resolved against each of
        them, else against each workspace root.
    """
    starts = list(cwds) or list(workspace_roots)
    starts = [start for start in starts if start and os.path.isabs(start)]
    state = _WalkState(
        cwd_outside_workspace=cwd_outside_workspace,
        base_dirs=[os.path.realpath(start) for start in starts],
    )
    reason = _command_violation(command, workspace_roots, depth=0, state=state)
    if reason is not None:
        return Decision(False, reason, command if isinstance(command, str) else None)
    return Decision(True, "local command passed allow-list", command)


def _command_violation(
    command: str, workspace_roots: Sequence[str], *, depth: int, state: _WalkState
) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return "empty command"
    if depth > _MAX_SHELL_DEPTH:
        return "shell nesting too deep"
    if "[redacted]" in command:
        return "command contained a credential"
    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        return "command substitution is not inspectable"
    if _EXPANSION_RE.search(command):
        return "shell variable expansion is not inspectable"
    if _DESTRUCTIVE_SQL_RE.search(command):
        return "destructive SQL"
    try:
        tokens = _tokenize(command)
    except ValueError:
        return "command does not parse"
    segments = _segments(tokens)
    if not segments:
        return "empty command"
    for segment in segments:
        reason = _segment_violation(segment, workspace_roots, depth, state)
        if reason is not None:
            return reason
    return None


def _tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _is_separator(token: str) -> bool:
    """Control operators, including merged runs such as ``);`` or ``|&``."""
    if token in _SEGMENT_SEPARATORS:
        return True
    if not token or not set(token) <= _PUNCTUATION_CHARS:
        return False
    return not _REDIRECT_RE.match(token) and token not in _INPUT_REDIRECTS


def _segments(tokens: Iterable[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if _is_separator(token):
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _segment_violation(
    segment: list[str], workspace_roots: Sequence[str], depth: int, state: _WalkState
) -> str | None:
    words: list[str] = []
    index = 0
    while index < len(segment):
        token = segment[index]
        if _REDIRECT_RE.match(token):
            target = segment[index + 1] if index + 1 < len(segment) else ""
            if not (target.isdigit() or target == "-" or _is_output_target_safe(target)):
                return "output redirection outside /tmp"
            index += 2
            continue
        if token.isdigit() and index + 1 < len(segment) and _REDIRECT_RE.match(segment[index + 1]):
            index += 1
            continue
        words.append(token)
        index += 1
    if not words:
        return None
    return _argv_violation(words, workspace_roots, depth, state)


def _is_output_target_safe(target: str) -> bool:
    if target in _FIXED_LOCAL_FILES:
        return True
    return os.path.isabs(target) and _is_under_roots(target, ())


def _argv_violation(
    words: list[str], workspace_roots: Sequence[str], depth: int, state: _WalkState
) -> str | None:
    """Check one simple command: program on the allow-list, arguments local."""
    while words and _ENV_ASSIGNMENT_RE.match(words[0]):
        if not _SAFE_ENV_NAME_RE.match(words[0].split("=", 1)[0]):
            return "environment override"
        words = words[1:]
    if not words:
        return None
    program, args = words[0], words[1:]
    if _SECRET_TOKEN_RE.search(program):
        return "secret-looking path or word"
    name = _program_name(program)
    if name is None:
        return "program path is not a trusted system binary"
    if name in _WRAPPERS:
        inner = _WRAPPERS[name](args)
        if isinstance(inner, str):
            return inner
        return _argv_violation(inner, workspace_roots, depth, state)
    if name in _SHELLS:
        script = _shell_script(args)
        if script is None:
            return f"{name} without an inspectable -c script"
        return _command_violation(script, workspace_roots, depth=depth + 1, state=state)
    if name == "gh":
        return "gh talks to a non-local host"
    if name in _INTERPRETERS:
        if _has_inline_code_flag(args):
            return f"inline code via {name}"
        return f"{name} is not on the allow-list"
    if name in _PROJECT_TOOLS and state.cwd_outside_workspace:
        return f"{name} with cwd outside the workspace"
    guard = _TOOL_GUARDS.get(name)
    workspace_guard = _WORKSPACE_TOOL_GUARDS.get(name)
    if guard is None and workspace_guard is None and name not in _PLAIN_TOOLS:
        return f"{name} is not on the allow-list"
    for token in args:
        reason = _token_violation(token, workspace_roots)
        if reason is not None:
            return reason
    if workspace_guard is not None:
        result = workspace_guard(args, _Scope(workspace_roots, tuple(state.base_dirs)))
    elif guard is not None:
        result = guard(args)
    else:
        return None
    if name == "cd" and result is None:
        _apply_cd(args[0], workspace_roots, state)
    if isinstance(result, list):
        return _argv_violation(result, workspace_roots, depth, state)
    return result


def _apply_cd(target: str, workspace_roots: Sequence[str], state: _WalkState) -> None:
    """Move the base dirs through ``cd target``, following symlinks.

    The cwd counts as outside the workspace when any candidate resolves
    outside the roots, so ``cd link`` to ``/tmp/x`` is caught.
    """
    if target.startswith("~"):
        target = os.path.expanduser(target)
    if os.path.isabs(target):
        state.base_dirs = [os.path.realpath(target)]
    elif state.base_dirs:
        state.base_dirs = [
            os.path.realpath(os.path.join(base, target)) for base in state.base_dirs
        ]
    else:
        return
    state.cwd_outside_workspace = any(
        not _is_under_roots(base, workspace_roots, include_fixed_roots=False)
        for base in state.base_dirs
    )


def _program_name(program: str) -> str | None:
    """Basename for a bare or trusted-path program, else ``None``."""
    if "$" in program or "~" in program:
        return None
    if "/" in program:
        directory = os.path.dirname(os.path.normpath(program))
        if directory not in _TRUSTED_BIN_DIRS:
            return None
        program = os.path.basename(program)
    return _VERSIONED_PROGRAM_RE.sub(r"\1", program)


def _short_flag_cluster(arg: str) -> frozenset[str] | None:
    """Letters of a ``-abc`` cluster, or ``None`` when *arg* is not one."""
    if re.fullmatch(r"-[A-Za-z]+", arg):
        return frozenset(arg[1:])
    return None


def _has_inline_code_flag(args: list[str]) -> bool:
    """True when an option before the script/module runs inline code.

    A combined cluster counts too, so ``-lc``, ``-Ic``, ``-we`` and ``-pe``
    are all inline code.
    """
    for arg in args:
        if arg in _INLINE_CODE_LONG_FLAGS:
            return True
        letters = _short_flag_cluster(arg)
        if letters is None:
            if arg.startswith("--"):
                continue
            return False
        if letters & _INLINE_CODE_LETTERS:
            return True
        if "m" in letters:
            return False
    return False


def _shell_script(args: list[str]) -> str | None:
    """Return the script of ``<shell> -<flags with c> '<script>'``, else ``None``."""
    has_command_flag = False
    for index, arg in enumerate(args):
        if arg in _SHELL_LONG_FLAGS:
            continue
        letters = _short_flag_cluster(arg)
        if letters is not None:
            if not letters <= _SHELL_FLAG_LETTERS:
                return None
            has_command_flag = has_command_flag or "c" in letters
            continue
        # Extra positional arguments after the script would become $0, $1...
        if not has_command_flag or index != len(args) - 1:
            return None
        return arg
    return None


# Wrappers return the inner argv to evaluate, or a reason to report.
WrapperResult = list[str] | str


def _unwrap_env(args: list[str]) -> WrapperResult:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-i", "--ignore-environment", "--"}:
            index += 1
        elif arg == "-u":
            index += 2
        elif arg.startswith("--unset="):
            index += 1
        elif _ENV_ASSIGNMENT_RE.match(arg):
            if not _SAFE_ENV_NAME_RE.match(arg.split("=", 1)[0]):
                return "environment override"
            index += 1
        elif arg.startswith("-"):
            return f"env option {arg} is not on the allow-list"
        else:
            return args[index:]
    return "env without a command prints the environment"


def _unwrap_nohup(args: list[str]) -> WrapperResult:
    return args if args else "nohup without a command"


def _unwrap_timeout(args: list[str]) -> WrapperResult:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        arg = args[index]
        if arg in {"-s", "-k"}:
            index += 2
        elif arg.startswith(("--signal=", "--kill-after=")) or arg in {
            "--foreground",
            "--preserve-status",
            "-v",
            "--verbose",
        }:
            index += 1
        else:
            return f"timeout option {arg} is not on the allow-list"
    if index >= len(args) or not _TIMEOUT_DURATION_RE.match(args[index]):
        return "timeout without a duration"
    inner = args[index + 1 :]
    return inner if inner else "timeout without a command"


def _unwrap_nice(args: list[str]) -> WrapperResult:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        arg = args[index]
        if arg == "-n":
            index += 2
        elif re.fullmatch(r"-\d+", arg) or arg.startswith("--adjustment="):
            index += 1
        else:
            return f"nice option {arg} is not on the allow-list"
    inner = args[index:]
    return inner if inner else "nice without a command"


_WRAPPERS: dict[str, Callable[[list[str]], WrapperResult]] = {
    "env": _unwrap_env,
    "nohup": _unwrap_nohup,
    "timeout": _unwrap_timeout,
    "nice": _unwrap_nice,
}


# Tool guards return ``None`` (allowed), a reason, or an inner argv to evaluate.
GuardResult = list[str] | str | None


def _guard_git(args: list[str], scope: _Scope) -> GuardResult:
    # -C / --git-dir / --work-tree pick whose .git/config runs (fsmonitor, diff.external).
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            value = args[index + 1] if index + 1 < len(args) else None
            reason = _workspace_dir_violation(value, scope)
            if reason is not None:
                return f"git {arg}: {reason}"
            index += 2
        elif arg.startswith(_GIT_GLOBAL_PATH_PREFIXES):
            name, value = arg.split("=", 1)
            reason = _workspace_dir_violation(value, scope)
            if reason is not None:
                return f"git {name}: {reason}"
            index += 1
        elif arg in _GIT_GLOBAL_FLAGS:
            index += 1
        elif arg.startswith("-"):
            return f"git option {arg} is not on the allow-list"
        else:
            break
    if index >= len(args):
        return None
    subcommand, sub_args = args[index], args[index + 1 :]
    if subcommand not in _GIT_ALLOWED_SUBCOMMANDS:
        return f"git {subcommand} is not on the allow-list"
    if any(arg.startswith("--output") for arg in sub_args):
        return "git --output writes a file"
    if subcommand == "grep" and any(
        arg == "-O" or arg.startswith(("-O", "--open-files-in-pager")) for arg in sub_args
    ):
        return "git grep --open-files-in-pager runs a program"
    if any(arg in {"--ext-diff", "--textconv"} for arg in sub_args):
        return "git external diff/textconv runs a program"
    return None


def _guard_cd(args: list[str]) -> GuardResult:
    # Bare cd and cd - jump to $HOME / OLDPWD, which later relative paths would hide.
    if len(args) != 1 or args[0] in {"", "-"} or args[0].startswith("-"):
        return "cd needs exactly one local directory"
    if not _path_is_local(args[0], ()) and not os.path.isabs(args[0]):
        return "cd target outside workspace and /tmp"
    return None


def _guard_find(args: list[str]) -> GuardResult:
    if any(arg in _FIND_ACTIONS_DENIED for arg in args):
        return "find with -delete/-exec/-fprint"
    return None


def _guard_rg(args: list[str]) -> GuardResult:
    if any(arg.startswith("--pre") for arg in args):
        return "rg --pre runs a program"
    return None


def _guard_network(args: list[str]) -> GuardResult:
    targets = [arg for arg in args if not arg.startswith("-")]
    if not targets:
        return "network tool without an inspectable target"
    for target in targets:
        candidate = target if "://" in target else f"//{target}"
        try:
            host = (urlsplit(candidate).hostname or "").lower()
        except ValueError:
            return "network target does not parse"
        if host not in LOOPBACK_HOSTS:
            return "network target outside localhost"
    return None


def _option_value(args: list[str], index: int) -> tuple[str, str | None, int]:
    """Split ``--opt value`` / ``--opt=value``: (name, value, next index)."""
    arg = args[index]
    if arg.startswith("--") and "=" in arg:
        name, value = arg.split("=", 1)
        return name, value, index + 1
    value = args[index + 1] if index + 1 < len(args) else None
    return arg, value, index + 2


def _workspace_dir_violation(value: str | None, scope: _Scope) -> str | None:
    """A project directory must resolve inside a workspace root; /tmp does not count.

    Relative values are joined to every base dir and resolved with realpath,
    so a workspace symlink to ``/tmp/x`` is outside. realpath also resolves
    every existing parent of a path that does not exist yet.
    """
    if not value or "$" in value:
        return "project directory is not inspectable"
    if value.startswith("~"):
        value = os.path.expanduser(value)
    if os.path.isabs(value):
        if _is_under_roots(value, scope.workspace_roots, include_fixed_roots=False):
            return None
        return "project directory outside workspace"
    normalized = os.path.normpath(value)
    if normalized == ".." or normalized.startswith("../"):
        return "project directory outside workspace"
    if not scope.base_dirs:
        return "project directory is not inspectable without a workspace"
    for base in scope.base_dirs:
        resolved = os.path.realpath(os.path.join(base, value))
        if not _is_under_roots(resolved, scope.workspace_roots, include_fixed_roots=False):
            return "project directory outside workspace"
    return None


def _guard_uv(args: list[str], scope: _Scope) -> GuardResult:
    index = 0
    seen_run = False
    while index < len(args):
        arg = args[index]
        name = arg.split("=", 1)[0] if arg.startswith("--") else arg
        if arg in _UV_FLAGS:
            index += 1
        elif name in _UV_OPTIONS_WITH_VALUE:
            name, value, index = _option_value(args, index)
            if name == "--package":
                if value is None or not _PACKAGE_NAME_RE.match(value):
                    return "uv --package needs a plain package name"
            else:
                reason = _workspace_dir_violation(value, scope)
                if reason is not None:
                    return reason
        elif arg.startswith("-"):
            return f"uv option {arg} is not on the allow-list"
        elif not seen_run:
            if arg != "run":
                return f"uv {arg} is not on the allow-list"
            seen_run = True
            index += 1
        else:
            return args[index:]
    return "uv without run <command>"


def _guard_node_package_manager(args: list[str], scope: _Scope) -> GuardResult:
    # npm/pnpm read config options on either side of the subcommand, so scan
    # every argument up to a literal "--" (after which they go to the script).
    index = 0
    subcommand: str | None = None
    while index < len(args):
        arg = args[index]
        if arg == "--" and subcommand is not None:
            return None
        name = arg.split("=", 1)[0] if arg.startswith("--") else arg
        if arg in _NODE_PM_FLAGS:
            index += 1
        elif name in _NODE_PM_OPTIONS_WITH_VALUE:
            name, value, index = _option_value(args, index)
            if name in _NODE_PM_DIRECTORY_OPTIONS:
                reason = _workspace_dir_violation(value, scope)
            elif value is None or value.startswith(("/", "~")) or ".." in value:
                reason = "package filter must name a workspace package"
            else:
                reason = None
            if reason is not None:
                return reason
        elif arg.startswith("-"):
            return f"package manager option {arg} is not on the allow-list"
        elif subcommand is None:
            if arg not in _NODE_PM_SUBCOMMANDS:
                return f"package manager {arg} is not on the allow-list"
            subcommand = arg
            index += 1
        else:
            index += 1
    return None if subcommand is not None else "package manager without a subcommand"


def _guard_pytest(args: list[str], scope: _Scope) -> GuardResult:
    """Test paths, config, rootdir, plugins and import paths stay in the workspace."""
    # pytest expands @file into more arguments (fromfile_prefix_chars); unreadable here.
    if any(arg.startswith("@") for arg in args):
        return "pytest @argfile reads arguments from a file"
    index = 0
    while index < len(args):
        arg = args[index]
        reason: str | None = None
        if arg.startswith("--") and arg != "--":
            name = arg.split("=", 1)[0]
            if name == "--basetemp":
                # pytest rm -rf's the basetemp directory before the run.
                return "pytest --basetemp deletes that directory"
            if name in _PYTEST_LONG_OPTIONS_WITH_VALUE:
                name, value, index = _option_value(args, index)
                reason = _pytest_option_violation(name, value, scope)
            elif arg in _PYTEST_LONG_FLAGS:
                index += 1
            else:
                return f"pytest option {name} is not on the allow-list"
        elif arg.startswith("-") and len(arg) > 1 and arg != "--":
            letter = arg[1]
            if letter in _PYTEST_SHORT_WITH_VALUE:
                # argparse reads -c=x as -c x; strip "=" so the value is checked as given.
                attached = arg[2:].lstrip("=")
                if arg[2:] and not attached:
                    return f"pytest {arg} without a value"
                value = attached or (args[index + 1] if index + 1 < len(args) else None)
                index += 1 if attached else 2
                reason = _pytest_option_violation(f"-{letter}", value, scope)
            elif set(arg[1:]) <= _PYTEST_SHORT_FLAGS:
                index += 1
            else:
                return f"pytest option {arg} is not on the allow-list"
        else:
            if arg != "--":
                reason = _workspace_dir_violation(arg.split("::", 1)[0], scope)
            index += 1
        if reason is not None:
            return reason
    return None


def _pytest_option_violation(name: str, value: str | None, scope: _Scope) -> str | None:
    if value is None:
        return f"pytest {name} without a value"
    if value.startswith("@"):
        return "pytest @argfile reads arguments from a file"
    if name in _PYTEST_PATH_OPTIONS:
        return _workspace_dir_violation(value, scope)
    if name in _PYTEST_OUTPUT_FILE_OPTIONS and not _is_output_target_safe(value):
        return f"pytest {name} writes outside /tmp"
    if name in {"-o", "--override-ini"}:
        key = value.split("=", 1)[0].strip().lower()
        if key in _PYTEST_RISKY_INI_KEYS:
            return f"pytest -o {key} can load code or config from elsewhere"
    if name == "-p" and not value.startswith("no:"):
        return "pytest -p loads a plugin"
    return None


def _guard_just(args: list[str]) -> GuardResult:
    for arg in args:
        if arg.startswith("-") and arg not in _JUST_FLAGS:
            return f"just option {arg} is not on the allow-list"
    return None


_TOOL_GUARDS: dict[str, Callable[[list[str]], GuardResult]] = {
    "cd": _guard_cd,
    "find": _guard_find,
    "rg": _guard_rg,
    "curl": _guard_network,
    "wget": _guard_network,
    "just": _guard_just,
}

# Guards that also need the workspace roots (project-directory options).
_WORKSPACE_TOOL_GUARDS: dict[str, Callable[[list[str], _Scope], GuardResult]] = {
    "uv": _guard_uv,
    "npm": _guard_node_package_manager,
    "pnpm": _guard_node_package_manager,
    "pytest": _guard_pytest,
    "git": _guard_git,
}


def _token_violation(token: str, workspace_roots: Sequence[str]) -> str | None:
    if _SECRET_TOKEN_RE.search(token):
        return "secret-looking path or word"
    if _SCP_REMOTE_RE.match(token):
        return "remote host target"
    for url in _URL_RE.findall(token):
        reason = _url_violation(url, workspace_roots)
        if reason is not None:
            return reason
    if _URL_RE.search(token):
        return None
    for candidate in _path_candidates(token):
        if "$" in candidate:
            return "unexpanded variable in path"
        if not _path_is_local(candidate, workspace_roots):
            return "path outside workspace and /tmp"
    return None


def _url_violation(url: str, workspace_roots: Sequence[str]) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparseable URL"
    if parts.scheme.lower() == "file":
        if _path_is_local(parts.path, workspace_roots):
            return None
        return "file URL outside workspace"
    if (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        return "network target outside localhost"
    return None


def _path_candidates(token: str) -> list[str]:
    values = [token]
    if (token.startswith("-") and "=" in token) or _ENV_ASSIGNMENT_RE.match(token):
        values = [token.split("=", 1)[1]]
    return [value for value in values if _looks_like_path(value)]


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if value in {".", ".."}:
        return True
    return value.startswith(("/", "~", "./", "../", "$")) or "/" in value


def _path_is_local(path: str, workspace_roots: Sequence[str]) -> bool:
    if path in _FIXED_LOCAL_FILES:
        return True
    if path.startswith("~"):
        return _is_under_roots(os.path.expanduser(path), workspace_roots)
    if os.path.isabs(path):
        return _is_under_roots(path, workspace_roots)
    # Relative to the agent's cwd; escaping it with .. is not inspectable.
    normalized = os.path.normpath(path)
    return normalized != ".." and not normalized.startswith("../")


def _is_under_roots(
    path: str, workspace_roots: Sequence[str], *, include_fixed_roots: bool = True
) -> bool:
    # Never resolve against the watcher's own cwd: relative paths and roots fail.
    if not path or not os.path.isabs(path):
        return False
    resolved = os.path.realpath(path)
    fixed_roots = _FIXED_LOCAL_ROOTS if include_fixed_roots else ()
    for root in (*workspace_roots, *fixed_roots):
        if not root or not os.path.isabs(root):
            continue
        real_root = os.path.realpath(root)
        if resolved == real_root or resolved.startswith(real_root.rstrip(os.sep) + os.sep):
            return True
    return False
