"""MCP tools beyond the core session drive.

Each tool calls an existing ``/v1`` route through :class:`omnigent.server.mcp_endpoint._Api`.
Writes return the planned request unless ``confirm`` is boolean true. Session file
upload stays off this surface: that route is multipart, not a JSON body.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

from omnigent.server.mcp_endpoint import (
    _CONFIRM_HELP,
    ToolFailure,
    _Api,
    _optional_str,
    _preview,
    _require_id,
    _require_present_str,
    _session_path,
    _with_query,
)

NOT_AN_EXTENSION = object()

_Handler = Callable[[_Api, dict[str, Any]], Awaitable[Any]]

_SESSION = {"type": "string", "description": "Session id."}
_CONFIRM = {"type": "boolean", "description": _CONFIRM_HELP}
_HOST = {"type": "string", "description": "Host id from list_hosts."}
_PROJECT = {"type": "string", "description": "Project id from list_projects."}


def extension_tool_definitions() -> list[dict[str, Any]]:
    """Tool descriptors appended to the core catalog."""
    return [
        _tool(
            "get_elicitation",
            "Read one elicitation. GET /v1/sessions/{session_id}/elicitations/{elicitation_id}.",
            {"session_id": _SESSION, "elicitation_id": {"type": "string"}},
            ["session_id", "elicitation_id"],
        ),
        _tool(
            "resolve_elicitation",
            "Answer an elicitation. POST .../elicitations/{elicitation_id}/resolve "
            "with action accept, decline, or cancel. Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "elicitation_id": {"type": "string"},
                "action": {"type": "string", "enum": ["accept", "decline", "cancel"]},
                "content": {"type": "object"},
                "confirm": _CONFIRM,
            },
            ["session_id", "elicitation_id", "action"],
        ),
        _tool(
            "list_session_files",
            "List files stored on a session. GET /v1/sessions/{session_id}/resources/files.",
            {"session_id": _SESSION, "limit": {"type": "integer"}},
            ["session_id"],
        ),
        _tool(
            "list_environment_changes",
            "List files changed in a session environment. "
            "GET .../resources/environments/{environment_id}/changes.",
            {"session_id": _SESSION, "environment_id": {"type": "string"}},
            ["session_id", "environment_id"],
        ),
        _tool(
            "get_environment_diff",
            "Read one environment file diff. GET .../environments/{environment_id}/diff/{path}.",
            {
                "session_id": _SESSION,
                "environment_id": {"type": "string"},
                "path": {"type": "string"},
            },
            ["session_id", "environment_id", "path"],
        ),
        _tool(
            "get_session_github",
            "GitHub repo, branch, and pull request for a session. "
            "GET /v1/sessions/{session_id}/resources/github.",
            {"session_id": _SESSION, "pr_url": {"type": "string"}},
            ["session_id"],
        ),
        _tool(
            "list_session_github_changes",
            "Files changed on the session pull request. GET .../resources/github/changes.",
            {"session_id": _SESSION, "pr_url": {"type": "string"}},
            ["session_id"],
        ),
        _tool(
            "attach_session_pull_request",
            "Attach a pull request to the session. POST .../resources/github/prs. "
            "Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "url": {"type": "string"},
                "action": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["session_id", "url"],
        ),
        _tool(
            "update_session",
            "Patch a session title, project, or archived flag. "
            "PATCH /v1/sessions/{session_id}. Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "title": {"type": "string"},
                "project_id": {
                    "type": "string",
                    "description": "Project id, or empty string to unfile.",
                },
                "archived": {"type": "boolean"},
                "confirm": _CONFIRM,
            },
            ["session_id"],
        ),
        _tool(
            "delete_session",
            "Delete a session. DELETE /v1/sessions/{session_id}. "
            "delete_branch also removes a server-created worktree branch. "
            "Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "delete_branch": {"type": "boolean"},
                "confirm": _CONFIRM,
            },
            ["session_id"],
        ),
        _tool(
            "list_comments",
            "List review comments on a session. GET /v1/sessions/{session_id}/comments.",
            {"session_id": _SESSION, "path": {"type": "string"}},
            ["session_id"],
        ),
        _tool(
            "add_comment",
            "Add a review comment. POST /v1/sessions/{session_id}/comments. "
            "Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "path": {"type": "string"},
                "body": {"type": "string"},
                "start_index": {"type": "integer"},
                "end_index": {"type": "integer"},
                "anchor_content": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["session_id", "path", "body", "start_index", "end_index"],
        ),
        _tool(
            "update_comment",
            "Patch a comment body or status. PATCH .../comments/{comment_id}. "
            "Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "comment_id": {"type": "string"},
                "body": {"type": "string"},
                "status": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["session_id", "comment_id"],
        ),
        _tool(
            "delete_comment",
            "Delete a review comment. DELETE .../comments/{comment_id}. "
            "Dry-run unless confirm is true.",
            {"session_id": _SESSION, "comment_id": {"type": "string"}, "confirm": _CONFIRM},
            ["session_id", "comment_id"],
        ),
        _tool(
            "list_host_filesystem",
            "List a directory on a host. GET /v1/hosts/{host_id}/filesystem[/{path}]. "
            "Offline and missing hosts return the route's error.",
            {"host_id": _HOST, "path": {"type": "string"}},
            ["host_id"],
        ),
        _tool(
            "list_host_worktrees",
            "List git worktrees of a repo on a host. GET /v1/hosts/{host_id}/worktrees?path=.",
            {"host_id": _HOST, "path": {"type": "string"}},
            ["host_id", "path"],
        ),
        _tool(
            "create_host_directory",
            "Create a directory on a host. POST /v1/hosts/{host_id}/directories. "
            "Dry-run unless confirm is true.",
            {"host_id": _HOST, "path": {"type": "string"}, "confirm": _CONFIRM},
            ["host_id", "path"],
        ),
        _tool(
            "get_project",
            "Read one project. GET /v1/projects/{project_id}.",
            {"project_id": _PROJECT},
            ["project_id"],
        ),
        _tool(
            "create_project",
            "Create a project. POST /v1/projects. Dry-run unless confirm is true.",
            {
                "name": {"type": "string"},
                "config": {"type": "object"},
                "confirm": _CONFIRM,
            },
            ["name"],
        ),
        _tool(
            "update_project",
            "Rename a project or replace its config. PATCH /v1/projects/{project_id}. "
            "Dry-run unless confirm is true.",
            {
                "project_id": _PROJECT,
                "name": {"type": "string"},
                "config": {"type": "object"},
                "confirm": _CONFIRM,
            },
            ["project_id"],
        ),
        _tool(
            "delete_project",
            "Delete a project. Member sessions stay, unfiled. "
            "DELETE /v1/projects/{project_id}. Dry-run unless confirm is true.",
            {"project_id": _PROJECT, "confirm": _CONFIRM},
            ["project_id"],
        ),
        _tool(
            "get_usage",
            "Usage for the caller. GET /v1/usage.",
            {},
            [],
        ),
        _tool(
            "list_session_policies",
            "List policies on a session. GET /v1/sessions/{session_id}/policies.",
            {"session_id": _SESSION},
            ["session_id"],
        ),
        _tool(
            "create_session_policy",
            "Add a session policy. POST /v1/sessions/{session_id}/policies. "
            "type is python or url. Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["python", "url"]},
                "handler": {"type": "string"},
                "confirm": _CONFIRM,
            },
            ["session_id", "name", "type", "handler"],
        ),
        _tool(
            "delete_session_policy",
            "Delete a session policy. DELETE .../policies/{policy_id}. "
            "Dry-run unless confirm is true.",
            {"session_id": _SESSION, "policy_id": {"type": "string"}, "confirm": _CONFIRM},
            ["session_id", "policy_id"],
        ),
        _tool(
            "list_policy_registry",
            "List built-in policy handlers. GET /v1/policy-registry.",
            {},
            [],
        ),
        _tool(
            "list_session_mcp_servers",
            "List MCP servers declared on the session agent. GET .../agent/mcp-servers.",
            {"session_id": _SESSION},
            ["session_id"],
        ),
        _tool(
            "add_session_mcp_server",
            "Declare an MCP server on the session agent. "
            "POST .../agent/mcp-servers. Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "name": {"type": "string"},
                "transport": {"type": "string", "enum": ["http", "stdio"]},
                "url": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "confirm": _CONFIRM,
            },
            ["session_id", "name", "transport"],
        ),
        _tool(
            "delete_session_mcp_server",
            "Remove an MCP server from the session agent. "
            "DELETE .../agent/mcp-servers/{name}. Dry-run unless confirm is true.",
            {"session_id": _SESSION, "name": {"type": "string"}, "confirm": _CONFIRM},
            ["session_id", "name"],
        ),
        _tool(
            "list_skills",
            "Discover skills for a session, or for a host, harness, and path. GET /v1/skills.",
            {
                "session_id": _SESSION,
                "host_id": _HOST,
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
            "list_session_permissions",
            "List grants on a session. GET /v1/sessions/{session_id}/permissions.",
            {"session_id": _SESSION},
            ["session_id"],
        ),
        _tool(
            "get_session_owner",
            "Read the session owner. GET /v1/sessions/{session_id}/owner.",
            {"session_id": _SESSION},
            ["session_id"],
        ),
        _tool(
            "grant_session_access",
            "Grant session access. PUT /v1/sessions/{session_id}/permissions. "
            "level is 1 read, 2 edit, or 3 manage. Dry-run unless confirm is true.",
            {
                "session_id": _SESSION,
                "user_id": {"type": "string"},
                "level": {"type": "integer", "minimum": 1, "maximum": 3},
                "confirm": _CONFIRM,
            },
            ["session_id", "user_id", "level"],
        ),
        _tool(
            "revoke_session_access",
            "Revoke a session grant. DELETE .../permissions/{user_id}. "
            "Dry-run unless confirm is true.",
            {"session_id": _SESSION, "user_id": {"type": "string"}, "confirm": _CONFIRM},
            ["session_id", "user_id"],
        ),
        _tool(
            "get_sharing",
            "Read server sharing settings. GET /v1/sharing.",
            {},
            [],
        ),
        _tool(
            "set_sharing",
            "Update server sharing settings. PUT /v1/sharing. "
            "A deployment-managed setting returns the route's refusal. "
            "Dry-run unless confirm is true.",
            {
                "sharing_mode": {"type": "string"},
                "public_sharing": {"type": "boolean"},
                "confirm": _CONFIRM,
            },
            [],
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
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


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


async def _write(api: _Api, args: Mapping[str, Any], method: str, path: str, body: Any) -> Any:
    payload = body if isinstance(body, dict) else {}
    if args.get("confirm") is not True:
        return _preview(method, path, payload)
    result = await api.call(method, path, payload)
    return {"ok": True} if result is None else result


async def _read(api: _Api, method: str, path: str) -> Any:
    return await api.call(method, path)


def _session(session_id: str, *parts: str) -> str:
    path = _session_path(session_id)
    for part in parts:
        path = f"{path}/{quote(part, safe='')}"
    return path


async def _get_elicitation(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api, "GET", _session(_sid(args), "elicitations", _part(args, "elicitation_id"))
    )


async def _resolve_elicitation(api: _Api, args: dict[str, Any]) -> Any:
    action = args.get("action")
    if action not in {"accept", "decline", "cancel"}:
        raise ToolFailure("action must be accept, decline, or cancel")
    body: dict[str, Any] = {"action": action}
    if "content" in args:
        content = args.get("content")
        if not isinstance(content, dict):
            raise ToolFailure("content must be an object")
        body["content"] = content
    path = _session(_sid(args), "elicitations", _part(args, "elicitation_id"), "resolve")
    return await _write(api, args, "POST", path, body)


async def _list_session_files(api: _Api, args: dict[str, Any]) -> Any:
    limit = args.get("limit")
    return await _read(
        api,
        "GET",
        _with_query(
            _session(_sid(args), "resources", "files"),
            {"limit": None if limit is None else _int(limit, "limit")},
        ),
    )


async def _list_environment_changes(api: _Api, args: dict[str, Any]) -> Any:
    path = _session(
        _sid(args), "resources", "environments", _part(args, "environment_id"), "changes"
    )
    return await _read(api, "GET", path)


async def _get_environment_diff(api: _Api, args: dict[str, Any]) -> Any:
    relative = _nonempty(args, "path").lstrip("/")
    path = (
        f"{_session(_sid(args), 'resources', 'environments', _part(args, 'environment_id'))}"
        f"/diff/{quote(relative, safe='/')}"
    )
    return await _read(api, "GET", path)


async def _get_session_github(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api,
        "GET",
        _with_query(
            _session(_sid(args), "resources", "github"),
            {"pr_url": _optional_str(args.get("pr_url"), "pr_url")},
        ),
    )


async def _list_session_github_changes(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api,
        "GET",
        _with_query(
            _session(_sid(args), "resources", "github", "changes"),
            {"pr_url": _optional_str(args.get("pr_url"), "pr_url")},
        ),
    )


async def _attach_session_pull_request(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {"url": _nonempty(args, "url"), "action": "attach"}
    action = _optional_str(args.get("action"), "action")
    if action:
        body["action"] = action
    return await _write(
        api, args, "POST", _session(_sid(args), "resources", "github", "prs"), body
    )


async def _update_session(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {}
    if "title" in args:
        body["title"] = _nonempty(args, "title")
    if "project_id" in args:
        project_id = args.get("project_id")
        if not isinstance(project_id, str):
            raise ToolFailure("project_id must be a string")
        body["project_id"] = project_id
    if "archived" in args:
        body["archived"] = _flag(args.get("archived"), "archived")
    if not body:
        raise ToolFailure("update_session needs title, project_id, or archived")
    return await _write(api, args, "PATCH", _session_path(_sid(args)), body)


async def _delete_session(api: _Api, args: dict[str, Any]) -> Any:
    delete_branch = args.get("delete_branch")
    if delete_branch is not None:
        delete_branch = _flag(delete_branch, "delete_branch")
    path = _with_query(_session_path(_sid(args)), {"delete_branch": delete_branch})
    return await _write(api, args, "DELETE", path, {})


async def _list_comments(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(
        api,
        "GET",
        _with_query(
            _session(_sid(args), "comments"),
            {"path": _optional_str(args.get("path"), "path")},
        ),
    )


async def _add_comment(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {
        "path": _nonempty(args, "path"),
        "body": _nonempty(args, "body"),
        "start_index": _int(args.get("start_index"), "start_index"),
        "end_index": _int(args.get("end_index"), "end_index"),
    }
    anchor = _optional_str(args.get("anchor_content"), "anchor_content")
    if anchor is not None:
        body["anchor_content"] = anchor
    return await _write(api, args, "POST", _session(_sid(args), "comments"), body)


async def _update_comment(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {}
    if "body" in args:
        body["body"] = _nonempty(args, "body")
    if "status" in args:
        body["status"] = _nonempty(args, "status")
    if not body:
        raise ToolFailure("update_comment needs body or status")
    path = _session(_sid(args), "comments", _part(args, "comment_id"))
    return await _write(api, args, "PATCH", path, body)


async def _delete_comment(api: _Api, args: dict[str, Any]) -> Any:
    return await _write(
        api, args, "DELETE", _session(_sid(args), "comments", _part(args, "comment_id")), {}
    )


async def _list_host_filesystem(api: _Api, args: dict[str, Any]) -> Any:
    host_id = _part(args, "host_id")
    path = f"/v1/hosts/{quote(host_id, safe='')}/filesystem"
    extra = _optional_str(args.get("path"), "path")
    if extra:
        path = f"{path}/{quote(extra.lstrip('/'), safe='/')}"
    return await _read(api, "GET", path)


async def _list_host_worktrees(api: _Api, args: dict[str, Any]) -> Any:
    host_id = _part(args, "host_id")
    return await _read(
        api,
        "GET",
        _with_query(
            f"/v1/hosts/{quote(host_id, safe='')}/worktrees",
            {"path": _nonempty(args, "path")},
        ),
    )


async def _create_host_directory(api: _Api, args: dict[str, Any]) -> Any:
    host_id = _part(args, "host_id")
    return await _write(
        api,
        args,
        "POST",
        f"/v1/hosts/{quote(host_id, safe='')}/directories",
        {"path": _nonempty(args, "path")},
    )


async def _get_project(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(api, "GET", f"/v1/projects/{quote(_part(args, 'project_id'), safe='')}")


async def _create_project(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {"name": _nonempty(args, "name")}
    if "config" in args:
        config = args.get("config")
        if not isinstance(config, dict):
            raise ToolFailure("config must be an object")
        body["config"] = config
    return await _write(api, args, "POST", "/v1/projects", body)


async def _update_project(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {}
    if "name" in args:
        body["name"] = _nonempty(args, "name")
    if "config" in args:
        config = args.get("config")
        if not isinstance(config, dict):
            raise ToolFailure("config must be an object")
        body["config"] = config
    if not body:
        raise ToolFailure("update_project needs name or config")
    path = f"/v1/projects/{quote(_part(args, 'project_id'), safe='')}"
    return await _write(api, args, "PATCH", path, body)


async def _delete_project(api: _Api, args: dict[str, Any]) -> Any:
    path = f"/v1/projects/{quote(_part(args, 'project_id'), safe='')}"
    return await _write(api, args, "DELETE", path, {})


async def _get_usage(api: _Api, args: dict[str, Any]) -> Any:
    del args
    return await _read(api, "GET", "/v1/usage")


async def _list_session_policies(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(api, "GET", _session(_sid(args), "policies"))


async def _create_session_policy(api: _Api, args: dict[str, Any]) -> Any:
    policy_type = args.get("type")
    if policy_type not in {"python", "url"}:
        raise ToolFailure("type must be python or url")
    body = {
        "name": _nonempty(args, "name"),
        "type": policy_type,
        "handler": _nonempty(args, "handler"),
    }
    return await _write(api, args, "POST", _session(_sid(args), "policies"), body)


async def _delete_session_policy(api: _Api, args: dict[str, Any]) -> Any:
    return await _write(
        api, args, "DELETE", _session(_sid(args), "policies", _part(args, "policy_id")), {}
    )


async def _list_policy_registry(api: _Api, args: dict[str, Any]) -> Any:
    del args
    return await _read(api, "GET", "/v1/policy-registry")


async def _list_session_mcp_servers(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(api, "GET", _session(_sid(args), "agent", "mcp-servers"))


async def _add_session_mcp_server(api: _Api, args: dict[str, Any]) -> Any:
    transport = args.get("transport")
    if transport not in {"http", "stdio"}:
        raise ToolFailure("transport must be http or stdio")
    body: dict[str, Any] = {"name": _nonempty(args, "name"), "transport": transport}
    if transport == "http":
        body["url"] = _nonempty(args, "url")
    else:
        body["command"] = _nonempty(args, "command")
        raw_args = args.get("args") or []
        if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
            raise ToolFailure("args must be a list of strings")
        body["args"] = raw_args
    return await _write(api, args, "POST", _session(_sid(args), "agent", "mcp-servers"), body)


async def _delete_session_mcp_server(api: _Api, args: dict[str, Any]) -> Any:
    return await _write(
        api,
        args,
        "DELETE",
        _session(_sid(args), "agent", "mcp-servers", _nonempty(args, "name")),
        {},
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
    return await _read(
        api, "GET", f"/v1/extensions/{quote(_nonempty(args, 'extension_id'), safe='')}"
    )


async def _list_session_permissions(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(api, "GET", _session(_sid(args), "permissions"))


async def _get_session_owner(api: _Api, args: dict[str, Any]) -> Any:
    return await _read(api, "GET", _session(_sid(args), "owner"))


async def _grant_session_access(api: _Api, args: dict[str, Any]) -> Any:
    level = _int(args.get("level"), "level")
    if level < 1 or level > 3:
        raise ToolFailure("level must be an integer from 1 to 3")
    body = {"user_id": _nonempty(args, "user_id"), "level": level}
    return await _write(api, args, "PUT", _session(_sid(args), "permissions"), body)


async def _revoke_session_access(api: _Api, args: dict[str, Any]) -> Any:
    user_id = _nonempty(args, "user_id")
    return await _write(api, args, "DELETE", _session(_sid(args), "permissions", user_id), {})


async def _get_sharing(api: _Api, args: dict[str, Any]) -> Any:
    del args
    return await _read(api, "GET", "/v1/sharing")


async def _set_sharing(api: _Api, args: dict[str, Any]) -> Any:
    body: dict[str, Any] = {}
    if "sharing_mode" in args:
        body["sharing_mode"] = _nonempty(args, "sharing_mode")
    if "public_sharing" in args:
        body["public_sharing"] = _flag(args.get("public_sharing"), "public_sharing")
    if not body:
        raise ToolFailure("set_sharing needs sharing_mode or public_sharing")
    return await _write(api, args, "PUT", "/v1/sharing", body)


_HANDLERS: dict[str, _Handler] = {
    "get_elicitation": _get_elicitation,
    "resolve_elicitation": _resolve_elicitation,
    "list_session_files": _list_session_files,
    "list_environment_changes": _list_environment_changes,
    "get_environment_diff": _get_environment_diff,
    "get_session_github": _get_session_github,
    "list_session_github_changes": _list_session_github_changes,
    "attach_session_pull_request": _attach_session_pull_request,
    "update_session": _update_session,
    "delete_session": _delete_session,
    "list_comments": _list_comments,
    "add_comment": _add_comment,
    "update_comment": _update_comment,
    "delete_comment": _delete_comment,
    "list_host_filesystem": _list_host_filesystem,
    "list_host_worktrees": _list_host_worktrees,
    "create_host_directory": _create_host_directory,
    "get_project": _get_project,
    "create_project": _create_project,
    "update_project": _update_project,
    "delete_project": _delete_project,
    "get_usage": _get_usage,
    "list_session_policies": _list_session_policies,
    "create_session_policy": _create_session_policy,
    "delete_session_policy": _delete_session_policy,
    "list_policy_registry": _list_policy_registry,
    "list_session_mcp_servers": _list_session_mcp_servers,
    "add_session_mcp_server": _add_session_mcp_server,
    "delete_session_mcp_server": _delete_session_mcp_server,
    "list_skills": _list_skills,
    "list_extensions": _list_extensions,
    "get_extension": _get_extension,
    "list_session_permissions": _list_session_permissions,
    "get_session_owner": _get_session_owner,
    "grant_session_access": _grant_session_access,
    "revoke_session_access": _revoke_session_access,
    "get_sharing": _get_sharing,
    "set_sharing": _set_sharing,
}
