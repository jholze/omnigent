"""MCP tools beyond the core session drive.

Each tool calls an existing ``/v1`` route through :class:`omnigent.server.mcp_endpoint._Api`.
Writes return the planned request unless ``confirm`` is boolean true. Deletes and
revokes add ``will_lose``, naming what that request destroys.

Not exposed, because the route does not do what a JSON tool would imply:

* Session file upload and agent-bundle replace are multipart, not JSON.
* ``comments/send`` marks comments addressed and returns ``formatted_message``.
  It does not post a turn; ``send_message`` does that.
* Labels have a GET route. Writes merge through ``PATCH /v1/sessions/{id}``.
* ``model-override/reset`` clears a selection only while ``expected_model_override``
  is still current. It is not an unconditional reset.
* Policy evaluate exists only per session. Global policies are ``type=python``
  and must name a registry handler.
* Environment and GitHub per-file diffs are mounted but omitted from ``openapi.json``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote, unquote

from omnigent.server.mcp_endpoint import (
    _CONFIRM_HELP,
    ToolFailure,
    _Api,
    _optional_str,
    _preview,
    _require_id,
    _require_present_str,
    _session_path,
    _timeout_seconds,
    _watch_posted,
    _with_query,
)

NOT_AN_EXTENSION = object()

_Handler = Callable[[_Api, dict[str, Any]], Awaitable[Any]]

_SESSION = {"type": "string", "description": "Session id."}
_CONFIRM = {"type": "boolean", "description": _CONFIRM_HELP}
_ENV = {
    "type": "string",
    "description": "Environment id. Defaults to default.",
}

_ELICIT_ACTIONS = {
    "accept": "accept",
    "approve": "accept",
    "decline": "decline",
    "deny": "decline",
    "cancel": "cancel",
}


def extension_tool_definitions() -> list[dict[str, Any]]:
    """Tool descriptors appended to the core catalog."""
    return [
        _tool(
            "get_elicitation",
            "Read one elicitation. GET .../elicitations/{elicitation_id}.",
            {"session_id": _SESSION, "elicitation_id": {"type": "string"}},
            ["session_id", "elicitation_id"],
        ),
        _tool(
            "answer_elicitation",
            "Answer a needs_input elicitation. approve/deny map to accept/decline. "
            "text is sent as content. wait:true reads the live stream after the verdict. "
            "Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "elicitation_id": {"type": "string"},
                "action": _enum(("accept", "approve", "decline", "deny", "cancel"), "Verdict."),
                "content": {"type": "object", "description": "Form fields. Merged with text."},
                "text": {"type": "string", "description": "Answer text. Stored as content.text."},
                "wait": {"type": "boolean", "description": "After confirm, wait for the turn."},
                "timeout_seconds": {"type": "number"},
                "confirm": _CONFIRM,
            },
            ["session_id", "elicitation_id", "action"],
        ),
        _tool(
            "session_files",
            "Session files: list, get, content, or delete. Upload is multipart and is not a tool. "
            "delete names the file in will_lose and needs confirm.",
            {
                "action": _enum(("list", "get", "content", "delete"), "What to do."),
                "session_id": _SESSION,
                "file_id": {"type": "string"},
                "limit": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "session_workspace",
            "Environment files: list, read, search, changes, diff, write, edit, delete, or shell. "
            "diff is mounted but omitted from openapi.json. "
            "write, edit, delete, and shell need confirm. delete names the path in will_lose.",
            {
                "action": _enum(
                    (
                        "list",
                        "read",
                        "search",
                        "changes",
                        "diff",
                        "write",
                        "edit",
                        "delete",
                        "shell",
                    ),
                    "What to do.",
                ),
                "session_id": _SESSION,
                "environment_id": _ENV,
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path, or host path.",
                },
                "base": _enum(("workspace", "host"), "host lists an absolute path."),
                "q": {"type": "string", "description": "Search substring."},
                "content": {"type": "string", "description": "Replacement file text for write."},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "command": {
                    "type": "string",
                    "description": "Shell command. Owner-only, no approval gate.",
                },
                "timeout": {"type": "number", "description": "Shell timeout in seconds."},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "session_github",
            "GitHub info, changes, diff, attach a pull request, or set account preferences. "
            "diff is mounted but omitted from openapi.json. "
            "attach_pr and preferences need confirm.",
            {
                "action": _enum(
                    ("info", "changes", "diff", "attach_pr", "preferences"), "What to do."
                ),
                "session_id": _SESSION,
                "path": {"type": "string", "description": "File path for a per-file diff."},
                "pr_url": {"type": "string"},
                "url": {"type": "string", "description": "Pull request URL for attach_pr."},
                "pr_action": {"type": "string", "description": "Defaults to attach."},
                "account": {"type": "string"},
                "remote": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "manage_session",
            "Rename, archive, set labels, model_override, workspace, or host_id, fork, "
            "delete, list children, read the agent, read labels, or compare-and-clear a "
            "model override. workspace and host_id rebind an idle session and clear its "
            "runner. Replacing the agent bundle is a tar.gz upload, not this tool. "
            "reset_model clears only while expected_model_override is current. "
            "update, fork, delete, and reset_model need confirm. delete names what is removed.",
            {
                "action": _enum(
                    ("update", "fork", "delete", "children", "agent", "labels", "reset_model"),
                    "What to do.",
                ),
                "session_id": _SESSION,
                "title": {"type": "string"},
                "archived": {"type": "boolean"},
                "project_id": {
                    "type": "string",
                    "description": "Project id, or empty string to unfile.",
                },
                "labels": {"type": "object", "description": "String labels merged by PATCH."},
                "model_override": {
                    "type": "string",
                    "description": "Set, or default/off/reset to clear.",
                },
                "workspace": {
                    "type": "string",
                    "description": "Absolute workspace path. Rebinds an idle session.",
                },
                "host_id": {
                    "type": "string",
                    "description": "Host id owned by the caller. Rebinds an idle session.",
                },
                "expected_model_override": {
                    "type": "string",
                    "description": "Selection reset_model clears, only if it is still current.",
                },
                "agent_id": {"type": "string", "description": "Built-in agent for a fork."},
                "host_type": _enum(("external", "managed"), "How a fork obtains its host."),
                "delete_branch": {
                    "type": "boolean",
                    "description": "Also remove a server-created worktree and branch.",
                },
                "limit": {"type": "integer", "description": "Page size for children."},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "session_comments",
            "List, add, update, or delete review comments. "
            "send marks the named comments addressed and returns formatted_message. "
            "It does not post a turn; pass that text to send_message. "
            "add, update, delete, and send need confirm. delete names the comment.",
            {
                "action": _enum(("list", "add", "update", "delete", "send"), "What to do."),
                "session_id": _SESSION,
                "comment_id": {"type": "string"},
                "comment_ids": {"type": "array", "items": {"type": "string"}},
                "path": {"type": "string"},
                "body": {"type": "string"},
                "status": {"type": "string"},
                "start_index": {"type": "integer"},
                "end_index": {"type": "integer"},
                "anchor_content": {"type": "string"},
                "instruction": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "host_fs",
            "List a host directory, list git worktrees, or create a directory. "
            "Needs a live host. mkdir needs confirm.",
            {
                "action": _enum(("list", "worktrees", "mkdir"), "What to do."),
                "host_id": {"type": "string", "description": "Host id from list_hosts."},
                "path": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["action", "host_id"],
        ),
        _tool(
            "project",
            "Get, create, update, delete, or reorder projects, or read /v1/usage. "
            "list_projects is separate. delete does not delete member sessions; "
            "the preview says they stay unfiled. Writes need confirm.",
            {
                "action": _enum(
                    ("get", "create", "update", "delete", "order", "usage"), "What to do."
                ),
                "project_id": {"type": "string"},
                "name": {"type": "string"},
                "config": {"type": "object"},
                "ordered_project_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Manual order. null selects alphabetical. Omit to read the order."
                    ),
                },
                "confirm": _CONFIRM,
            },
            ["action"],
        ),
        _tool(
            "policy",
            "Global or per-session policies: list, get, create, update, delete, evaluate, "
            "or the handler registry. Evaluate is per session only and can rewrite content. "
            "Global create is type python and a registry handler. Admin writes need confirm. "
            "delete names the policy that stops applying.",
            {
                "action": _enum(
                    ("list", "get", "create", "update", "delete", "evaluate", "registry"),
                    "What to do.",
                ),
                "scope": _enum(
                    ("session", "global"), "Defaults to session. registry ignores this."
                ),
                "session_id": _SESSION,
                "policy_id": {"type": "string"},
                "name": {"type": "string"},
                "type": _enum(("python", "url"), "Global policies only accept python."),
                "handler": {"type": "string"},
                "factory_params": {"type": "object"},
                "enabled": {"type": "boolean"},
                "event": {
                    "type": "object",
                    "description": "EvaluationRequest event, including type.",
                },
                "confirm": _CONFIRM,
            },
            ["action"],
        ),
        _tool(
            "session_mcp",
            "List, create, update, or delete MCP servers on the session agent. "
            "Writes change the agent bundle and need confirm. delete names the server removed.",
            {
                "action": _enum(("list", "create", "update", "delete"), "What to do."),
                "session_id": _SESSION,
                "name": {"type": "string"},
                "transport": _enum(("http", "stdio"), "http needs url. stdio needs command."),
                "url": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "list_skills",
            "Read skills. Pass session_id, or host_id plus harness and path.",
            {
                "session_id": _SESSION,
                "host_id": {"type": "string"},
                "harness": {"type": "string"},
                "path": {"type": "string"},
            },
            [],
        ),
        _tool(
            "list_extensions",
            "List installed extensions. GET /v1/extensions.",
            {},
            [],
        ),
        _tool(
            "get_extension",
            "Read one extension. GET /v1/extensions/{extension_id}.",
            {"extension_id": {"type": "string"}},
            ["extension_id"],
        ),
        _tool(
            "session_access",
            "List grants, read the owner, grant access, or revoke it. "
            "grant and revoke need confirm. revoke names the user who loses access.",
            {
                "action": _enum(("list", "owner", "grant", "revoke"), "What to do."),
                "session_id": _SESSION,
                "user_id": {"type": "string"},
                "level": {"type": "integer", "description": "1 read, 2 edit, 3 manage."},
                "confirm": _CONFIRM,
            },
            ["action", "session_id"],
        ),
        _tool(
            "sharing",
            "Read sharing settings, or change them. set needs confirm. "
            "A deployment-managed setting is refused by the route.",
            {
                "action": _enum(("get", "set"), "What to do."),
                "sharing_mode": {"type": "string"},
                "public_sharing": {"type": "boolean"},
                "public_sharing_max_level": {"type": "string"},
                "default_public_sessions": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["action"],
        ),
    ]


async def call_extension(api: _Api, name: str, args: dict[str, Any]) -> Any:
    """Run one extension tool, or :data:`NOT_AN_EXTENSION` when ``name`` is not one."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return NOT_AN_EXTENSION
    return await handler(api, args)


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def _enum(values: tuple[str, ...], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": description}


def _sid(args: Mapping[str, Any]) -> str:
    return _require_id(_require_present_str(args.get("session_id"), "session_id"), "session_id")


def _part(args: Mapping[str, Any], name: str) -> str:
    return _require_id(_require_present_str(args.get(name), name), name)


def _nonempty(args: Mapping[str, Any], name: str) -> str:
    value = _require_present_str(args.get(name), name)
    if not value.strip():
        raise ToolFailure(f"{name} is required")
    return value


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolFailure(f"{name} must be an integer")
    return value


def _flag(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ToolFailure(f"{name} must be a boolean")
    return value


def _action(args: Mapping[str, Any], allowed: tuple[str, ...]) -> str:
    action = args.get("action")
    if action not in allowed:
        raise ToolFailure(f"action must be one of {', '.join(allowed)}")
    return str(action)


def _object(args: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = args.get(name)
    if not isinstance(value, dict):
        raise ToolFailure(f"{name} must be an object")
    return value


def _string_map(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise ToolFailure(f"{name} must be an object of strings")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ToolFailure(f"{name} must be a list of strings")
    return value


def _env_id(args: Mapping[str, Any]) -> str:
    if args.get("environment_id") is None:
        return "default"
    return _part(args, "environment_id")


def _optional_flag(args: Mapping[str, Any], name: str) -> bool | None:
    if name not in args or args.get(name) is None:
        return None
    return _flag(args.get(name), name)


async def _write(
    api: _Api,
    args: Mapping[str, Any],
    method: str,
    path: str,
    body: Any,
    *,
    loss: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    payload = body if isinstance(body, dict) else {}
    if args.get("confirm") is not True:
        preview: dict[str, Any] = {}
        if loss:
            preview["will_lose"] = loss
        if extra:
            preview.update(extra)
        return _preview(method, path, payload, preview or None)
    result = await api.call(method, path, payload)
    return {"ok": True} if result is None else result


async def _read(api: _Api, method: str, path: str) -> Any:
    return await api.call(method, path)


def _session(session_id: str, *parts: str) -> str:
    path = _session_path(session_id)
    for part in parts:
        path = f"{path}/{quote(part, safe='')}"
    return path


def _check_fs_path(relative: str, name: str = "path") -> str:
    """Reject a filesystem path httpx would send somewhere else.

    ``.`` and ``..`` segments, including percent-encoded ones, are rejected.
    httpx collapses dot segments, so ``DELETE .../filesystem/../../../..``
    would be sent as ``DELETE /v1/sessions/{id}`` while the preview still
    showed the original path. A single leading slash is kept so a host
    absolute path such as ``/tmp/oa-mcp-dir`` can stay in a JSON body.

    :param relative: Caller-supplied path, relative or absolute.
    :param name: Argument name used in the tool error.
    :returns: ``relative`` unchanged when every segment is safe.
    :raises ToolFailure: The path is empty or contains an unsafe segment.
    """
    if not relative or "\\" in relative or "\x00" in relative or relative.startswith("//"):
        raise ToolFailure(f"{name} contains invalid characters")
    body = relative[1:] if relative.startswith("/") else relative
    if not body or body.endswith("/"):
        raise ToolFailure(f"{name} contains invalid characters")
    for segment in body.split("/"):
        if segment in {"", ".", ".."} or unquote(segment) in {".", ".."}:
            raise ToolFailure(f"{name} contains invalid characters")
    return relative


def _encode_url_path(relative: str) -> str:
    """Percent-encode each segment of a path that survived :func:`_check_fs_path`.

    ``safe=''`` encodes everything except unreserved characters, so the
    string appended to the preview is the path httpx will request.
    """
    checked = _check_fs_path(relative)
    body = checked[1:] if checked.startswith("/") else checked
    return "/".join(quote(part, safe="") for part in body.split("/"))


def _joined(base: str, relative: str | None) -> str:
    """Append ``relative`` to ``base`` as the exact URL that will be requested."""
    if not relative:
        return base
    return f"{base}/{_encode_url_path(relative)}"


async def _get_elicitation(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api, "GET", _session(_sid(args), "elicitations", _part(args, "elicitation_id"))
    )


async def _answer_elicitation(api: _Api, args: dict[str, Any]) -> Any:
    raw = args.get("action")
    if raw not in _ELICIT_ACTIONS:
        raise ToolFailure("action must be accept, approve, decline, deny, or cancel")
    body: dict[str, Any] = {"action": _ELICIT_ACTIONS[str(raw)]}
    content: dict[str, Any] | None = None
    if "content" in args and args.get("content") is not None:
        supplied = args.get("content")
        if not isinstance(supplied, dict):
            raise ToolFailure("content must be an object")
        content = dict(supplied)
    text = _optional_str(args.get("text"), "text")
    if text:
        content = {} if content is None else content
        content.setdefault("text", text)
    if content is not None:
        body["content"] = content
    wait = _optional_flag(args, "wait") or False
    budget = _timeout_seconds(args.get("timeout_seconds")) if wait else None
    session_id = _sid(args)
    path = _session(session_id, "elicitations", _part(args, "elicitation_id"), "resolve")
    extra: dict[str, Any] = {
        "effect": "Posts this elicitation verdict. approve means accept and deny means decline."
    }
    if wait and budget is not None:
        extra["wait"] = {
            "stream": f"GET {_session_path(session_id)}/stream?idle=false",
            "timeout_seconds": budget,
        }
    if args.get("confirm") is not True:
        return _preview("POST", path, body, extra)

    async def post() -> Any:
        return await api.call("POST", path, body)

    if not wait or budget is None:
        return await post()
    watched = await _watch_posted(api, session_id, post, budget)
    watched["resolved"] = watched.pop("accepted")
    return watched


async def _session_files(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "get", "content", "delete"))
    session_id = _sid(args)
    if action == "list":
        limit = args.get("limit")
        return await _read(
            api,
            "GET",
            _with_query(
                _session(session_id, "resources", "files"),
                {"limit": None if limit is None else _int(limit, "limit")},
            ),
        )
    file_id = _part(args, "file_id")
    path = _session(session_id, "resources", "files", file_id)
    if action == "get":
        return await _read(api, "GET", path)
    if action == "content":
        payload = await _read(api, "GET", f"{path}/content")
        if isinstance(payload, str):
            return {"content": payload}
        return payload
    return await _write(
        api,
        args,
        "DELETE",
        path,
        {},
        loss=f"Deletes session file {file_id} and its stored bytes.",
    )


async def _session_workspace(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(
        args, ("list", "read", "search", "changes", "diff", "write", "edit", "delete", "shell")
    )
    session_id = _sid(args)
    environment_id = _env_id(args)
    root = _session(session_id, "resources", "environments", environment_id)
    relative = _optional_str(args.get("path"), "path")
    base = args.get("base")
    if base not in {None, "workspace", "host"}:
        raise ToolFailure("base must be workspace or host")
    query: dict[str, Any] = {"base": base if base == "host" else None}
    if action in {"list", "read"}:
        kind = "filesystem"
        if action == "read" and not relative:
            raise ToolFailure("read needs path")
        target = relative if action == "read" or relative else None
        return await _read(api, "GET", _with_query(_joined(f"{root}/{kind}", target), query))
    if action == "search":
        return await _read(
            api,
            "GET",
            _with_query(
                _joined(f"{root}/search", relative),
                {**query, "q": _nonempty(args, "q")},
            ),
        )
    if action == "changes":
        return await _read(api, "GET", _with_query(f"{root}/changes", query))
    if action == "diff":
        if not relative:
            raise ToolFailure("diff needs path")
        return await _read(api, "GET", _with_query(_joined(f"{root}/diff", relative), query))
    if action == "shell":
        body: dict[str, Any] = {"command": _nonempty(args, "command")}
        if "timeout" in args and args.get("timeout") is not None:
            timeout = args.get("timeout")
            if isinstance(timeout, bool) or not isinstance(timeout, int | float):
                raise ToolFailure("timeout must be a number")
            body["timeout"] = timeout
        return await _write(
            api,
            args,
            "POST",
            f"{root}/shell",
            body,
            extra={
                "effect": (
                    "Runs this command in the session environment as the owner. "
                    "There is no approval gate."
                )
            },
        )
    if not relative:
        raise ToolFailure(f"{action} needs path")
    target = _joined(f"{root}/filesystem", relative)
    if action == "write":
        return await _write(
            api,
            args,
            "PUT",
            target,
            {"content": _require_present_str(args.get("content"), "content")},
            extra={"effect": f"Replaces the file at {relative} in environment {environment_id}."},
        )
    if action == "edit":
        return await _write(
            api,
            args,
            "PATCH",
            target,
            {
                "old_text": _require_present_str(args.get("old_text"), "old_text"),
                "new_text": _require_present_str(args.get("new_text"), "new_text"),
            },
            extra={"effect": f"Replaces old_text with new_text in {relative}."},
        )
    return await _write(
        api,
        args,
        "DELETE",
        target,
        {},
        loss=(
            f"Deletes {relative} in environment {environment_id}. "
            "A directory delete removes everything under that path."
        ),
    )


async def _session_github(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("info", "changes", "diff", "attach_pr", "preferences"))
    session_id = _sid(args)
    pr_url = _optional_str(args.get("pr_url"), "pr_url")
    if action == "info":
        return await _read(
            api,
            "GET",
            _with_query(_session(session_id, "resources", "github"), {"pr_url": pr_url}),
        )
    if action == "changes":
        return await _read(
            api,
            "GET",
            _with_query(
                _session(session_id, "resources", "github", "changes"), {"pr_url": pr_url}
            ),
        )
    if action == "diff":
        path = _session(session_id, "resources", "github", "diff")
        relative = _optional_str(args.get("path"), "path")
        return await _read(api, "GET", _with_query(_joined(path, relative), {"pr_url": pr_url}))
    if action == "attach_pr":
        body: dict[str, Any] = {"url": _nonempty(args, "url"), "action": "attach"}
        pr_action = _optional_str(args.get("pr_action"), "pr_action")
        if pr_action:
            body["action"] = pr_action
        return await _write(
            api,
            args,
            "POST",
            _session(session_id, "resources", "github", "prs"),
            body,
            extra={"effect": "Attaches or updates the pull request recorded for this session."},
        )
    body = {}
    account = _optional_str(args.get("account"), "account")
    remote = _optional_str(args.get("remote"), "remote")
    if account is not None:
        body["account"] = account
    if remote is not None:
        body["remote"] = remote
    if pr_url:
        body["pr_url"] = pr_url
    if not body:
        raise ToolFailure("preferences needs account, remote, or pr_url")
    return await _write(
        api,
        args,
        "POST",
        _session(session_id, "resources", "github", "preferences"),
        body,
        extra={"effect": "Saves the GitHub account or base selection for this workspace."},
    )


async def _manage_session(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(
        args, ("update", "fork", "delete", "children", "agent", "labels", "reset_model")
    )
    session_id = _sid(args)
    if action == "children":
        limit = args.get("limit")
        return await _read(
            api,
            "GET",
            _with_query(
                _session(session_id, "child_sessions"),
                {"limit": None if limit is None else _int(limit, "limit")},
            ),
        )
    if action == "agent":
        return await _read(api, "GET", _session(session_id, "agent"))
    if action == "labels":
        return await _read(api, "GET", _session(session_id, "labels"))
    if action == "reset_model":
        expected = _nonempty(args, "expected_model_override")
        return await _write(
            api,
            args,
            "POST",
            _session(session_id, "model-override", "reset"),
            {"expected_model_override": expected},
            extra={
                "effect": (
                    f"Clears model override {expected!r} only while that selection is still "
                    "current. A newer selection is left in place."
                )
            },
        )
    if action == "fork":
        body: dict[str, Any] = {}
        title = _optional_str(args.get("title"), "title")
        agent_id = _optional_str(args.get("agent_id"), "agent_id")
        host_type = args.get("host_type")
        if title:
            body["title"] = title
        if agent_id:
            body["agent_id"] = agent_id
        if host_type is not None:
            if host_type not in {"external", "managed"}:
                raise ToolFailure("host_type must be external or managed")
            body["host_type"] = host_type
        return await _write(
            api,
            args,
            "POST",
            _session(session_id, "fork"),
            body,
            extra={
                "effect": (
                    "Creates a new session with a copy of this session's items and a cloned "
                    "agent. The source session stays."
                )
            },
        )
    if action == "delete":
        delete_branch = _optional_flag(args, "delete_branch")
        loss = (
            f"Deletes session {session_id}: its conversation, items, session files, and "
            "runner resources."
        )
        if delete_branch:
            loss += " delete_branch also removes the server-created worktree and its branch."
        path = _with_query(_session_path(session_id), {"delete_branch": delete_branch})
        return await _write(api, args, "DELETE", path, {}, loss=loss)
    body = _session_patch(args)
    if not body:
        raise ToolFailure(
            "update needs title, archived, project_id, labels, model_override, "
            "workspace, or host_id"
        )
    return await _write(
        api,
        args,
        "PATCH",
        _session_path(session_id),
        body,
        extra={
            "effect": (
                "Patches this session. labels merge; omitted fields stay as they are. "
                "workspace and host_id rebind an idle session, update its composer "
                "working directory, and clear a stale runner so the next message "
                "launches on the new host and path."
            )
        },
    )


def _session_patch(args: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if "title" in args and args.get("title") is not None:
        body["title"] = _nonempty(args, "title")
    if "project_id" in args:
        project_id = args.get("project_id")
        if not isinstance(project_id, str):
            raise ToolFailure("project_id must be a string")
        body["project_id"] = project_id
    if "archived" in args and args.get("archived") is not None:
        body["archived"] = _flag(args.get("archived"), "archived")
    if "labels" in args and args.get("labels") is not None:
        body["labels"] = _string_map(args.get("labels"), "labels")
    if "model_override" in args and args.get("model_override") is not None:
        body["model_override"] = _nonempty(args, "model_override")
    if "workspace" in args and args.get("workspace") is not None:
        body["workspace"] = _nonempty(args, "workspace")
    if "host_id" in args and args.get("host_id") is not None:
        body["host_id"] = _nonempty(args, "host_id")
    return body


async def _session_comments(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "add", "update", "delete", "send"))
    session_id = _sid(args)
    if action == "list":
        return await _read(
            api,
            "GET",
            _with_query(
                _session(session_id, "comments"),
                {"path": _optional_str(args.get("path"), "path")},
            ),
        )
    if action == "add":
        body: dict[str, Any] = {
            "path": _nonempty(args, "path"),
            "body": _nonempty(args, "body"),
            "start_index": _int(args.get("start_index"), "start_index"),
            "end_index": _int(args.get("end_index"), "end_index"),
        }
        anchor = _optional_str(args.get("anchor_content"), "anchor_content")
        if anchor is not None:
            body["anchor_content"] = anchor
        return await _write(api, args, "POST", _session(session_id, "comments"), body)
    if action == "send":
        comment_ids = _string_list(args.get("comment_ids"), "comment_ids")
        if not comment_ids:
            raise ToolFailure("comment_ids is required")
        body = {"comment_ids": comment_ids}
        instruction = _optional_str(args.get("instruction"), "instruction")
        if instruction:
            body["instruction"] = instruction
        return await _write(
            api,
            args,
            "POST",
            _session(session_id, "comments", "send"),
            body,
            extra={
                "effect": (
                    "Marks these comments addressed and returns formatted_message. "
                    "Does not post a turn. Pass formatted_message to send_message to deliver it."
                )
            },
        )
    comment_id = _part(args, "comment_id")
    path = _session(session_id, "comments", comment_id)
    if action == "delete":
        return await _write(
            api,
            args,
            "DELETE",
            path,
            {},
            loss=f"Deletes comment {comment_id} on session {session_id}, including its body.",
        )
    body = {}
    if "body" in args and args.get("body") is not None:
        body["body"] = _nonempty(args, "body")
    if "status" in args and args.get("status") is not None:
        body["status"] = _nonempty(args, "status")
    if not body:
        raise ToolFailure("update needs body or status")
    return await _write(api, args, "PATCH", path, body)


async def _host_fs(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "worktrees", "mkdir"))
    host_id = _part(args, "host_id")
    root = f"/v1/hosts/{quote(host_id, safe='')}"
    if action == "worktrees":
        host_path = _check_fs_path(_nonempty(args, "path"))
        return await _read(api, "GET", _with_query(f"{root}/worktrees", {"path": host_path}))
    relative = _optional_str(args.get("path"), "path")
    if relative is not None:
        relative = _check_fs_path(relative)
    if action == "list":
        return await _read(api, "GET", _joined(f"{root}/filesystem", relative))
    if not relative:
        raise ToolFailure("mkdir needs path")
    return await _write(
        api,
        args,
        "POST",
        f"{root}/directories",
        {"path": relative},
        extra={"effect": f"Creates directory {relative} on host {host_id}."},
    )


async def _project(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("get", "create", "update", "delete", "order", "usage"))
    if action == "usage":
        return await _read(api, "GET", "/v1/usage")
    if action == "order" and "ordered_project_ids" not in args:
        return await _read(api, "GET", "/v1/projects/order")
    if action == "order":
        ids = args.get("ordered_project_ids")
        if ids is not None:
            ids = _string_list(ids, "ordered_project_ids")
        return await _write(
            api,
            args,
            "PUT",
            "/v1/projects/order",
            {"ordered_project_ids": ids},
            extra={
                "effect": (
                    "Saves this project order. null selects alphabetical mode and keeps "
                    "the remembered ids."
                )
            },
        )
    if action == "create":
        body: dict[str, Any] = {"name": _nonempty(args, "name")}
        if "config" in args and args.get("config") is not None:
            body["config"] = _object(args, "config")
        return await _write(api, args, "POST", "/v1/projects", body)
    project_id = _part(args, "project_id")
    path = f"/v1/projects/{quote(project_id, safe='')}"
    if action == "get":
        return await _read(api, "GET", path)
    if action == "delete":
        return await _write(
            api,
            args,
            "DELETE",
            path,
            {},
            loss=(
                f"Deletes project {project_id}. Member sessions are not deleted; "
                "they stay unfiled."
            ),
        )
    body = {}
    if "name" in args and args.get("name") is not None:
        body["name"] = _nonempty(args, "name")
    if "config" in args and args.get("config") is not None:
        body["config"] = _object(args, "config")
    if not body:
        raise ToolFailure("update needs name or config")
    return await _write(api, args, "PATCH", path, body)


async def _policy(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "get", "create", "update", "delete", "evaluate", "registry"))
    if action == "registry":
        return await _read(api, "GET", "/v1/policy-registry")
    scope = args.get("scope") or "session"
    if scope not in {"session", "global"}:
        raise ToolFailure("scope must be session or global")
    if action == "evaluate" and scope != "session":
        raise ToolFailure("evaluate is per session; there is no global evaluate route")
    session_id = _sid(args) if scope == "session" else None
    collection = _session(session_id, "policies") if session_id is not None else "/v1/policies"
    if action == "list":
        return await _read(api, "GET", collection)
    if action == "evaluate":
        event = _object(args, "event")
        return await _write(
            api,
            args,
            "POST",
            f"{collection}/evaluate",
            {"event": event},
            extra={
                "effect": (
                    "Evaluates policies for this session. A policy can rewrite the event content."
                )
            },
        )
    if action == "create":
        policy_type = args.get("type")
        if policy_type not in {"python", "url"}:
            raise ToolFailure("type must be python or url")
        body: dict[str, Any] = {
            "name": _nonempty(args, "name"),
            "type": policy_type,
            "handler": _nonempty(args, "handler"),
        }
        if "factory_params" in args and args.get("factory_params") is not None:
            body["factory_params"] = _object(args, "factory_params")
        where = "this session" if scope == "session" else "every session"
        return await _write(
            api,
            args,
            "POST",
            collection,
            body,
            extra={
                "effect": (
                    f"Creates a {scope} policy that applies to {where}. "
                    "Global policies must be type python and a registry handler."
                )
            },
        )
    policy_id = _part(args, "policy_id")
    path = f"{collection}/{quote(policy_id, safe='')}"
    if action == "get":
        return await _read(api, "GET", path)
    if action == "delete":
        if scope == "session":
            loss = (
                f"Deletes session policy {policy_id} on session {session_id}. "
                "That gate stops applying to the session."
            )
        else:
            loss = (
                f"Deletes global default policy {policy_id}. "
                "Every session stops receiving that default."
            )
        return await _write(api, args, "DELETE", path, {}, loss=loss)
    body = {}
    if "name" in args and args.get("name") is not None:
        body["name"] = _nonempty(args, "name")
    if "handler" in args and args.get("handler") is not None:
        body["handler"] = _nonempty(args, "handler")
    if "enabled" in args and args.get("enabled") is not None:
        body["enabled"] = _flag(args.get("enabled"), "enabled")
    if not body:
        raise ToolFailure("update needs name, handler, or enabled")
    return await _write(
        api,
        args,
        "PATCH",
        path,
        body,
        extra={"effect": f"Updates {scope} policy {policy_id}. type cannot be changed."},
    )


def _mcp_body(args: Mapping[str, Any]) -> dict[str, Any]:
    transport = args.get("transport")
    if transport not in {"http", "stdio"}:
        raise ToolFailure("transport must be http or stdio")
    body: dict[str, Any] = {"name": _nonempty(args, "name"), "transport": transport}
    description = _optional_str(args.get("description"), "description")
    if description:
        body["description"] = description
    if transport == "http":
        body["url"] = _nonempty(args, "url")
    else:
        body["command"] = _nonempty(args, "command")
        if "args" in args and args.get("args") is not None:
            body["args"] = _string_list(args.get("args"), "args")
    return body


async def _session_mcp(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "create", "update", "delete"))
    session_id = _sid(args)
    collection = _session(session_id, "agent", "mcp-servers")
    if action == "list":
        return await _read(api, "GET", collection)
    if action == "create":
        body = _mcp_body(args)
        return await _write(
            api,
            args,
            "POST",
            collection,
            body,
            extra={"effect": f"Adds MCP server {body['name']} to this session's agent bundle."},
        )
    name = _require_id(_nonempty(args, "name"), "name")
    path = _session(session_id, "agent", "mcp-servers", name)
    if action == "delete":
        return await _write(
            api,
            args,
            "DELETE",
            path,
            {},
            loss=(
                f"Removes MCP server {name} from the session agent bundle. "
                "The agent loses that server's tools."
            ),
        )
    return await _write(
        api,
        args,
        "PUT",
        path,
        _mcp_body(args),
        extra={"effect": f"Replaces MCP server {name} on this session's agent bundle."},
    )


async def _list_skills(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api,
        "GET",
        _with_query(
            "/v1/skills",
            {
                "session_id": _optional_str(args.get("session_id"), "session_id"),
                "host_id": _optional_str(args.get("host_id"), "host_id"),
                "harness": _optional_str(args.get("harness"), "harness"),
                "path": _optional_str(args.get("path"), "path"),
            },
        ),
    )


async def _list_extensions(api: _Api, args: dict[str, Any]) -> Any:
    del args
    return await _read(api, "GET", "/v1/extensions")


async def _get_extension(api: _Api, args: dict[str, Any]) -> Any:
    extension_id = _require_id(_nonempty(args, "extension_id"), "extension_id")
    return await _read(api, "GET", f"/v1/extensions/{quote(extension_id, safe='')}")


async def _session_access(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("list", "owner", "grant", "revoke"))
    session_id = _sid(args)
    if action == "list":
        return await _read(api, "GET", _session(session_id, "permissions"))
    if action == "owner":
        return await _read(api, "GET", _session(session_id, "owner"))
    user_id = _require_id(_nonempty(args, "user_id"), "user_id")
    if action == "revoke":
        return await _write(
            api,
            args,
            "DELETE",
            _session(session_id, "permissions", user_id),
            {},
            loss=(
                f"Revokes {user_id}'s grant on session {session_id}. "
                "That user loses the access level they had."
            ),
        )
    level = _int(args.get("level"), "level")
    if level < 1 or level > 3:
        raise ToolFailure("level must be an integer from 1 to 3")
    return await _write(
        api,
        args,
        "PUT",
        _session(session_id, "permissions"),
        {"user_id": user_id, "level": level},
        extra={"effect": f"Grants {user_id} level {level} on session {session_id}."},
    )


async def _sharing(api: _Api, args: dict[str, Any]) -> Any:
    action = _action(args, ("get", "set"))
    if action == "get":
        return await _read(api, "GET", "/v1/sharing")
    body: dict[str, Any] = {}
    if "sharing_mode" in args and args.get("sharing_mode") is not None:
        body["sharing_mode"] = _nonempty(args, "sharing_mode")
    if "public_sharing" in args and args.get("public_sharing") is not None:
        body["public_sharing"] = _flag(args.get("public_sharing"), "public_sharing")
    for name in ("public_sharing_max_level", "default_public_sessions"):
        if name in args and args.get(name) is not None:
            body[name] = _nonempty(args, name)
    if not body:
        raise ToolFailure("set needs a sharing field")
    return await _write(
        api,
        args,
        "PUT",
        "/v1/sharing",
        body,
        extra={"effect": "Changes server sharing settings for later sessions."},
    )


_HANDLERS: dict[str, _Handler] = {
    "get_elicitation": _get_elicitation,
    "answer_elicitation": _answer_elicitation,
    "session_files": _session_files,
    "session_workspace": _session_workspace,
    "session_github": _session_github,
    "manage_session": _manage_session,
    "session_comments": _session_comments,
    "host_fs": _host_fs,
    "project": _project,
    "policy": _policy,
    "session_mcp": _session_mcp,
    "list_skills": _list_skills,
    "list_extensions": _list_extensions,
    "get_extension": _get_extension,
    "session_access": _session_access,
    "sharing": _sharing,
}
