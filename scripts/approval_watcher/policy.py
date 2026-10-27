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
from dataclasses import dataclass
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
        "pytest",
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
    cwd = data.get("cwd", request.get("cwd"))
    if cwd is not None:
        if not isinstance(cwd, str) or not _is_under_roots(cwd, workspace_roots):
            return Decision(False, "card cwd outside workspace", command)
    decision = evaluate_command(command, workspace_roots=workspace_roots)
    return Decision(decision.approve, decision.reason, command)


def evaluate_command(command: str, *, workspace_roots: Sequence[str]) -> Decision:
    """Apply the allow-list to one shell command string."""
    reason = _command_violation(command, workspace_roots, depth=0)
    if reason is not None:
        return Decision(False, reason, command if isinstance(command, str) else None)
    return Decision(True, "local command passed allow-list", command)


def _command_violation(command: str, workspace_roots: Sequence[str], *, depth: int) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return "empty command"
    if depth > _MAX_SHELL_DEPTH:
        return "shell nesting too deep"
    if "[redacted]" in command:
        return "command contained a credential"
    if "$(" in command or "`" in command or "<(" in command or ">(" in command:
        return "command substitution is not inspectable"
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
        reason = _segment_violation(segment, workspace_roots, depth)
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
    segment: list[str], workspace_roots: Sequence[str], depth: int
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
    return _argv_violation(words, workspace_roots, depth)


def _is_output_target_safe(target: str) -> bool:
    if target in _FIXED_LOCAL_FILES:
        return True
    return os.path.isabs(target) and _is_under_roots(target, ())


def _argv_violation(words: list[str], workspace_roots: Sequence[str], depth: int) -> str | None:
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
        return _argv_violation(inner, workspace_roots, depth)
    if name in _SHELLS:
        script = _shell_script(args)
        if script is None:
            return f"{name} without an inspectable -c script"
        return _command_violation(script, workspace_roots, depth=depth + 1)
    if name == "gh":
        return "gh talks to a non-local host"
    if name in _INTERPRETERS:
        if _has_inline_code_flag(args):
            return f"inline code via {name}"
        return f"{name} is not on the allow-list"
    guard = _TOOL_GUARDS.get(name)
    if guard is None and name not in _PLAIN_TOOLS:
        return f"{name} is not on the allow-list"
    for token in args:
        reason = _token_violation(token, workspace_roots)
        if reason is not None:
            return reason
    if guard is None:
        return None
    result = guard(args)
    if isinstance(result, list):
        return _argv_violation(result, workspace_roots, depth)
    return result


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


def _guard_git(args: list[str]) -> GuardResult:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
        elif arg in _GIT_GLOBAL_FLAGS or arg.startswith(_GIT_GLOBAL_PATH_PREFIXES):
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


def _guard_uv(args: list[str]) -> GuardResult:
    index = 0
    seen_run = False
    while index < len(args):
        arg = args[index]
        if arg in _UV_FLAGS:
            index += 1
        elif arg in _UV_OPTIONS_WITH_VALUE:
            index += 2
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


def _guard_node_package_manager(args: list[str]) -> GuardResult:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _NODE_PM_FLAGS:
            index += 1
        elif arg in _NODE_PM_OPTIONS_WITH_VALUE:
            index += 2
        elif arg.startswith("-"):
            return f"package manager option {arg} is not on the allow-list"
        elif arg in _NODE_PM_SUBCOMMANDS:
            return None
        else:
            return f"package manager {arg} is not on the allow-list"
    return "package manager without a subcommand"


def _guard_just(args: list[str]) -> GuardResult:
    for arg in args:
        if arg.startswith("-") and arg not in _JUST_FLAGS:
            return f"just option {arg} is not on the allow-list"
    return None


_TOOL_GUARDS: dict[str, Callable[[list[str]], GuardResult]] = {
    "cd": _guard_cd,
    "git": _guard_git,
    "find": _guard_find,
    "rg": _guard_rg,
    "curl": _guard_network,
    "wget": _guard_network,
    "uv": _guard_uv,
    "npm": _guard_node_package_manager,
    "pnpm": _guard_node_package_manager,
    "just": _guard_just,
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


def _is_under_roots(path: str, workspace_roots: Sequence[str]) -> bool:
    if path.startswith("~"):
        return False
    resolved = os.path.realpath(path)
    for root in (*workspace_roots, *_FIXED_LOCAL_ROOTS):
        if not root:
            continue
        real_root = os.path.realpath(root)
        if resolved == real_root or resolved.startswith(real_root.rstrip(os.sep) + os.sep):
            return True
    return False
