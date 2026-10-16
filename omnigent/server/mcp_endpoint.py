"""Streamable HTTP MCP endpoint mounted on the Omnigent server.

External agents (Claude Code, Cursor, remote cloud agents) call ``POST /mcp``
with JSON-RPC and the same credentials as ``/v1``. Reads run immediately.
Every write returns the request it would make unless ``confirm`` is boolean
true. Deletes and revokes also name what that request would destroy.

Tool calls go through the existing HTTP routes on this process
(:class:`httpx.ASGITransport`), forwarding the caller's credentials and
``Origin`` unchanged, so session permissions, validation, and event dispatch
stay in the routes that already own them. ``POST /mcp`` itself requires a
JSON content type and the same trusted-origin check as ``/v1``. Waiting for
a reply reads the session (the live stream's read check) before subscribing
to the in-process stream that ``GET /v1/sessions/{id}/stream`` serves, and
the subscription is opened before the message POST so the turn's
``session.status`` is not missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes._content_type import require_json_content_type
from omnigent.server.routes._origin import require_trusted_origin
from omnigent.version import VERSION

_logger = logging.getLogger(__name__)

# Shared by every write tool. Boolean true is required; the string "true" is not.
_CONFIRM_HELP = "Perform the request. Default false returns the request and sends nothing."

_MCP_ENV = "OMNIGENT_MCP_ENABLED"
_COMPOSER_LABEL_PREFIX = "omnigent.composer_context.v1"
_AGENT_FIELD_HINT = (
    "agent_id is data[].id from list_agents; agent_name is data[].name from list_agents"
)
_HARD_TERMINAL = frozenset(
    {"response.failed", "response.cancelled", "turn.failed", "turn.cancelled"}
)
_UNSET = object()
# How often a waiting tool notices the MCP client has gone away.
_DISCONNECT_SLICE_S = 0.25


class ToolFailure(Exception):
    """A tool failed; the JSON-RPC call still succeeds with ``isError``."""


class _RpcFailure(Exception):
    """A JSON-RPC error response, distinct from a tool error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.rpc_code = code


_MCP_ON = frozenset({"1", "true", "yes", "on"})
_MCP_OFF = frozenset({"0", "false", "no", "off"})
# Versions this server can speak. An unknown client version gets the latest,
# not an echo of the request (MCP lifecycle negotiation).
_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26")
_LATEST_PROTOCOL = _PROTOCOL_VERSIONS[-1]


def _mcp_bool(raw: str) -> bool | None:
    """Parse ``on``/``off`` the same way for the env var and the config key."""
    value = raw.strip().lower()
    if value in _MCP_ON:
        return True
    if value in _MCP_OFF:
        return False
    return None


def mcp_endpoint_enabled(config: Mapping[str, Any]) -> bool:
    """Whether ``POST /mcp`` serves MCP calls.

    Default on. ``POST /mcp`` requires ``Content-Type: application/json`` and
    the same trusted-Origin rule as ``/v1``, then the same authentication, so
    a cross-site ``text/plain`` request cannot drive it. ``OMNIGENT_MCP_ENABLED``
    overrides the ``mcp_enabled`` config key. Both accept ``1``/``true``/``yes``/``on``
    and ``0``/``false``/``no``/``off``.

    :param config: Resolved server config. An absent ``mcp_enabled`` key stays on.
    :returns: ``True`` when the endpoint should accept MCP calls.
    """
    raw = os.environ.get(_MCP_ENV)
    if raw is not None and raw.strip():
        parsed = _mcp_bool(raw)
        return False if parsed is None else parsed
    value = config.get("mcp_enabled", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        parsed = _mcp_bool(value)
        return False if parsed is None else parsed
    if isinstance(value, int) and not isinstance(value, bool):
        return value != 0
    return True


def working_directory_from_labels(labels: Mapping[str, Any] | None) -> str | None:
    """Read the composer working directory out of session labels.

    The web client stores ``omnigent.composer_context.v1`` JSON, split into
    ``omnigent.composer_context.v1.0``, ``.1``, … when it exceeds one label
    value. A single unchunked key is also accepted.

    :param labels: Session label map from ``GET /v1/sessions``.
    :returns: The selected directory path, or ``None`` when the label is absent
        or not the version-1 composer document.
    """
    if not labels:
        return None
    raw = labels.get(_COMPOSER_LABEL_PREFIX)
    if not isinstance(raw, str) or not raw:
        chunks: list[tuple[int, str]] = []
        prefix = f"{_COMPOSER_LABEL_PREFIX}."
        for key, value in labels.items():
            if (
                not isinstance(key, str)
                or not key.startswith(prefix)
                or not isinstance(value, str)
            ):
                continue
            index = key[len(prefix) :]
            if index.isdigit():
                chunks.append((int(index), value))
        raw = "".join(value for _, value in sorted(chunks))
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    working_directory = parsed.get("working_directory")
    if not isinstance(working_directory, dict):
        return None
    path = working_directory.get("path")
    return path if isinstance(path, str) and path else None


def tool_definitions() -> list[dict[str, Any]]:
    """Return the MCP tool list advertised by ``tools/list``."""
    from omnigent.server.mcp_extensions import extension_tool_definitions

    core: list[dict[str, Any]] = [
        {
            "name": "health",
            "description": (
                "Liveness probe. GET /health. Returns {status: ok} when the server is up."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "info",
            "description": (
                "Runtime capabilities. GET /v1/info. Auth mode, server version, "
                "single-user flag, sandbox providers."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_agents",
            "description": (
                "List agents. GET /v1/agents. Each item has id and name. "
                "scope=user lists the caller's own agents. create_session accepts either field."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "after": {
                        "type": "string",
                        "description": "Cursor. Return agents after this id.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user"],
                        "description": "Set to user for the caller's agents.",
                    },
                },
            },
        },
        {
            "name": "list_hosts",
            "description": "List hosts owned by the caller, online and offline. GET /v1/hosts.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_harnesses",
            "description": "List installed harnesses. GET /v1/harnesses.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_projects",
            "description": (
                "List the caller's projects. GET /v1/projects. "
                "Each row is id, name, workspace, and default_agent "
                "(the project's config.agentId and config.workspace)."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list_sessions",
            "description": (
                "List sessions. GET /v1/sessions. visibility is always sent "
                "(all, mine, shared, or archived; default all). "
                "project or project_id filters to one project (id from list_projects, "
                "or the project name; empty string lists sessions not in a project). "
                "Rows are compact: id, title, status, agent_name, project_id, "
                "working_directory (from the omnigent.composer_context.v1.0 label), updated_at."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "after": {"type": "string"},
                    "before": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "agent_name": {"type": "string"},
                    "search_query": {
                        "type": "string",
                        "description": "Case-insensitive match on title or conversation text.",
                    },
                    "include_archived": {"type": "boolean"},
                    "order": {"type": "string", "enum": ["asc", "desc"]},
                    "sort_by": {"type": "string", "enum": ["created_at", "updated_at"]},
                    "project": {
                        "type": "string",
                        "description": "Project id or name. Empty string lists unfiled sessions.",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Alias of project when the value is a list_projects id.",
                    },
                    "kind": {"type": "string", "enum": ["default", "sub_agent", "any"]},
                    "visibility": {
                        "type": "string",
                        "enum": ["all", "mine", "shared", "archived"],
                    },
                },
            },
        },
        {
            "name": "get_session",
            "description": (
                "Fetch one session. GET /v1/sessions/{session_id}. "
                "The embedded items array is omitted; use get_session_items for the transcript."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
        {
            "name": "get_session_items",
            "description": (
                "Conversation transcript. GET /v1/sessions/{session_id}/items. "
                "Default page is the newest 30 items (order=desc). Text is truncated."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Default 30.",
                    },
                    "after": {"type": "string"},
                    "before": {"type": "string"},
                    "order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "desc (default) is newest first. asc is chronological.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 200,
                        "maximum": 20000,
                        "description": "Max characters kept per text block. Default 2000.",
                    },
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "create_session",
            "description": (
                "Create a session bound to an agent. POST /v1/sessions. "
                "Dry-run unless confirm is true: returns the request and changes nothing. "
                "Pass agent_id or agent_name from list_agents. "
                "A name is resolved with GET /v1/agents before the preview or the POST. "
                "Optional message is stored as initial_items. To wait for the reply, "
                "create without a message, then call send_message with wait."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Durable agent id from list_agents data[].id.",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": (
                            "Agent name from list_agents. Resolved when agent_id is omitted."
                        ),
                    },
                    "name": {"type": "string", "description": "Alias of agent_name."},
                    "host_id": {
                        "type": "string",
                        "description": (
                            "External host to launch on. Required with workspace "
                            "to start the runner."
                        ),
                    },
                    "workspace": {
                        "type": "string",
                        "description": (
                            "Absolute path on an external host, or a git URL "
                            "when host_type is managed."
                        ),
                    },
                    "host_type": {"type": "string", "enum": ["external", "managed"]},
                    "title": {"type": "string"},
                    "message": {"type": "string", "description": "Optional first user message."},
                    "confirm": {"type": "boolean", "description": _CONFIRM_HELP},
                },
            },
        },
        {
            "name": "send_message",
            "description": (
                "Post a user message into a session. POST /v1/sessions/{session_id}/events "
                "with type message and an input_text block. Dry-run unless confirm is true. "
                "wait subscribes to the session live stream (the same events as "
                "GET /v1/sessions/{id}/stream?idle=false) before the POST and returns when "
                "session.status settles to idle or failed, when an elicitation arrives, "
                "or at timeout_seconds (default 120)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "text": {"type": "string"},
                    "wait": {
                        "type": "boolean",
                        "description": "Read the live stream until the turn ends. Default false.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Wait budget in seconds. Default 120, max 600.",
                    },
                    "confirm": {"type": "boolean", "description": _CONFIRM_HELP},
                },
                "required": ["session_id", "text"],
            },
        },
        {
            "name": "interrupt_turn",
            "description": (
                "Cancel the in-flight turn without deleting the session. "
                "POST /v1/sessions/{session_id}/events {type: interrupt}. "
                "Dry-run unless confirm is true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "confirm": {"type": "boolean", "description": _CONFIRM_HELP},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "stop_session",
            "description": (
                "Stop the live session process without deleting the conversation. "
                "POST /v1/sessions/{session_id}/events {type: stop_session}. "
                "Owner-only on the server. The next message can relaunch it. "
                "Dry-run unless confirm is true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "confirm": {"type": "boolean", "description": _CONFIRM_HELP},
                },
                "required": ["session_id"],
            },
        },
    ]
    for tool in core:
        tool["inputSchema"]["additionalProperties"] = False
    return core + extension_tool_definitions()


def create_mcp_router(auth_provider: AuthProvider | None) -> APIRouter:
    """Build the ``/mcp`` router.

    The route stays mounted when the endpoint is disabled so a turned-off
    flag returns JSON 404 instead of the SPA shell. Unauthenticated calls
    are 401, matching ``require_user`` on ``/v1``.

    :param auth_provider: The app's auth provider. ``None`` is the no-auth
        single-user test and embed posture, where other routes also skip auth.
    :returns: A router with ``GET``/``POST``/``DELETE /mcp``.
    """
    router = APIRouter()

    @router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def mcp_http(request: Request) -> Response:
        """Serve one Streamable HTTP MCP message, or 401/404/405."""
        if not getattr(request.app.state, "mcp_enabled", False):
            raise OmnigentError("MCP endpoint is disabled", code=ErrorCode.NOT_FOUND)
        # Same CSRF boundary as /v1, checked here so the inner call cannot
        # re-label a text/plain body as JSON or swap in a first-party Origin.
        if request.method == "POST":
            require_json_content_type(request)
            require_trusted_origin(request)
        require_user(request, auth_provider)
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        return await _handle_post(request, auth_provider)

    return router


async def _handle_post(request: Request, auth_provider: AuthProvider | None) -> Response:
    raw = await request.body()
    try:
        message = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return _rpc_error(None, -32700, "Parse error", status=400)
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid request", status=400)
    has_id = "id" in message and message["id"] is not None
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        if has_id:
            return _rpc_error(message.get("id"), -32600, "Invalid request", status=400)
        return _rpc_error(None, -32600, "Invalid request", status=400)
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error(message.get("id"), -32602, "Invalid params", status=400)
    method = message["method"]
    if not has_id and method.startswith("notifications/"):
        return Response(status_code=202)
    try:
        result = await _dispatch(request, auth_provider, method, params)
    except _RpcFailure as exc:
        if not has_id:
            return Response(status_code=202)
        return _rpc_error(message["id"], exc.rpc_code, str(exc))
    except ToolFailure as exc:
        if not has_id:
            return Response(status_code=202)
        if method != "tools/call":
            return _rpc_error(message["id"], -32603, str(exc))
        result = _tool_result(str(exc), is_error=True)
    except OmnigentError:
        raise
    except Exception:  # one bad call must stay a JSON-RPC error
        _logger.exception("mcp: %s failed", method)
        if not has_id:
            return Response(status_code=202)
        return _rpc_error(message["id"], -32603, "Internal error")
    if not has_id:
        return Response(status_code=202)
    response = JSONResponse({"jsonrpc": "2.0", "id": message["id"], "result": result})
    if method == "initialize" and isinstance(result, dict):
        version = result.get("protocolVersion")
        if isinstance(version, str) and version:
            response.headers["MCP-Protocol-Version"] = version
    return response


async def _dispatch(
    request: Request,
    auth_provider: AuthProvider | None,
    method: str,
    params: dict[str, Any],
) -> Any:
    if method == "initialize":
        return _initialize_result(params)
    if method == "ping":
        return {}
    if method.startswith("notifications/"):
        return {}
    if method == "tools/list":
        return {"tools": tool_definitions()}
    if method == "resources/list":
        return {"resources": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "tools/call":
        return await _perform_call(request, auth_provider, params)
    raise _RpcFailure(-32601, f"Method not found: {method}")


def _initialize_result(params: Mapping[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    if isinstance(requested, str) and requested in _PROTOCOL_VERSIONS:
        version = requested
    else:
        version = _LATEST_PROTOCOL
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "omnigent", "version": VERSION},
        "instructions": (
            "Drives this Omnigent server as the authenticated user. "
            "Every write tool does nothing unless confirm is boolean true; "
            "it returns the method, path, and body it would send. "
            "Deletes and revokes include will_lose, naming what would be destroyed. "
            "A user message is POST /v1/sessions/{id}/events with type message. "
            "send_message wait:true reads the session live stream until the turn ends. "
            "list_projects returns project id, name, workspace, and default agent; "
            "list_sessions accepts that id as project."
        ),
    }


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


async def _perform_call(
    request: Request,
    auth_provider: AuthProvider | None,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        return _tool_result("tool name is required", is_error=True)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _tool_result("arguments must be an object", is_error=True)
    try:
        data = await _call_tool(request, auth_provider, name, args)
    except ToolFailure as exc:
        return _tool_result(str(exc), is_error=True)
    return _tool_result(json.dumps(data, indent=2))


def _rpc_error(rpc_id: Any, code: int, message: str, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}},
        status_code=status,
    )


class _Api:
    """In-process client for the app that received the MCP call."""

    def __init__(self, request: Request, auth_provider: AuthProvider | None) -> None:
        self._app = request.app
        self._request = request
        self._headers = _forwarded_headers(request, auth_provider)

    async def call(
        self, method: str, path: str, body: Any = _UNSET, *, timeout: float = 30.0
    ) -> Any:
        headers = dict(self._headers)
        content: bytes | None = None
        if body is not _UNSET:
            headers["content-type"] = "application/json"
            content = json.dumps(body).encode()
        transport = httpx.ASGITransport(app=self._app, raise_app_exceptions=False)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://omnigent"
            ) as client:
                response = await client.request(
                    method, path, headers=headers, content=content, timeout=timeout
                )
        except httpx.TimeoutException as exc:
            raise ToolFailure(f"Omnigent {method} {path} timed out") from exc
        except httpx.HTTPError as exc:
            raise ToolFailure(f"Omnigent {method} {path} failed: {exc}") from exc
        text = response.text
        if response.status_code >= 400:
            raise ToolFailure(
                f"Omnigent {method} {path} returned HTTP {response.status_code}: {_snippet(text)}"
            )
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def _forwarded_headers(request: Request, auth_provider: AuthProvider | None) -> dict[str, str]:
    """Copy the caller's credentials onto the in-process route call.

    The inner route runs ``require_user`` again, so the acting user is the
    MCP caller. ``Origin`` is forwarded unchanged when the caller sent one,
    and omitted when they did not. It is not replaced with the first-party
    sentinel; ``/mcp`` already applied :func:`require_trusted_origin`.
    """
    headers = {"accept": "application/json"}
    origin = request.headers.get("origin")
    if origin:
        headers["origin"] = origin
    for name in ("authorization", "cookie"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    names = ["x-forwarded-email"]
    header_name = getattr(auth_provider, "_header_name", None)
    if isinstance(header_name, str) and header_name.lower() not in names:
        names.append(header_name)
    for name in names:
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _snippet(text: str) -> str:
    if len(text) <= 1500:
        return text
    return f"{text[:1500]}…"


def _with_query(path: str, params: Mapping[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if value == "" and key != "project":
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        pairs.append((key, rendered))
    if not pairs:
        return path
    return f"{path}?{urlencode(pairs)}"


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolFailure(f"{name} must be a string")
    return value


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _require_id(value: str, name: str) -> str:
    """Reject a value that is not one safe URL path segment.

    ``.`` and ``..`` are rejected as well as ``/``. httpx collapses dot
    segments, so ``/v1/sessions/..`` would be sent as ``DELETE /v1`` even
    though the preview showed the original path.
    """
    if not value:
        raise ToolFailure(f"{name} is required")
    if value in {".", ".."} or any(char in value for char in " \t\r\n/?#\\"):
        raise ToolFailure(f"{name} contains invalid characters")
    return value


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ToolFailure("text is required")
    return value


def _optional_int(value: Any, name: str, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ToolFailure(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _one_of(value: Any, name: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise ToolFailure(f"{name} must be one of {', '.join(allowed)}")
    return str(value)


def _timeout_seconds(value: Any) -> float:
    if value is None:
        return 120.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolFailure("timeout_seconds must be a number in (0, 600]")
    if not math.isfinite(float(value)) or value <= 0 or value > 600:
        raise ToolFailure("timeout_seconds must be a number in (0, 600]")
    return float(value)


def _user_message_event(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "data": {"role": "user", "content": [{"type": "input_text", "text": text}]},
    }


def _preview(
    method: str, path: str, body: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    planned: dict[str, Any] = {"dry_run": True, "method": method, "path": path, "body": body}
    if extra:
        planned.update(extra)
    return planned


def _present_session(payload: Any) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload
    items = payload["items"]
    rest = {key: value for key, value in payload.items() if key != "items"}
    rest["items_omitted"] = True
    rest["item_count"] = len(items)
    return rest


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…[truncated]"


def _truncate_block(block: Any, max_chars: int) -> Any:
    if not isinstance(block, dict) or not isinstance(block.get("text"), str):
        return block
    if len(block["text"]) <= max_chars:
        return block
    return {**block, "text": _clip(block["text"], max_chars), "truncated": True}


def _truncate_item(item: Any, max_chars: int) -> Any:
    if not isinstance(item, dict):
        return item
    copy = dict(item)
    if isinstance(copy.get("content"), list):
        copy["content"] = [_truncate_block(block, max_chars) for block in copy["content"]]
    data = copy.get("data")
    if isinstance(data, dict):
        data_copy = dict(data)
        if isinstance(data_copy.get("content"), list):
            data_copy["content"] = [
                _truncate_block(block, max_chars) for block in data_copy["content"]
            ]
        copy["data"] = data_copy
    for key in ("arguments", "output"):
        if isinstance(copy.get(key), str):
            copy[key] = _clip(copy[key], max_chars)
    return copy


def _present_items(payload: Any, max_chars: int) -> Any:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    return {
        "object": payload.get("object", "list"),
        "data": [_truncate_item(item, max_chars) for item in data]
        if isinstance(data, list)
        else data,
        "first_id": payload.get("first_id"),
        "last_id": payload.get("last_id"),
        "has_more": bool(payload.get("has_more")),
    }


def _compact_session(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "status": item.get("status"),
        "agent_name": item.get("agent_name"),
        "project_id": item.get("project_id"),
        "working_directory": working_directory_from_labels(labels),
        "updated_at": item.get("updated_at"),
    }


def _compact_sessions(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    return {
        "object": payload.get("object", "list"),
        "data": [_compact_session(item) for item in data] if isinstance(data, list) else data,
        "first_id": payload.get("first_id"),
        "last_id": payload.get("last_id"),
        "has_more": bool(payload.get("has_more")),
    }


def _present_projects(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    rows: list[dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list):
        for project in data:
            if not isinstance(project, dict):
                continue
            config = project.get("config") if isinstance(project.get("config"), dict) else {}
            workspace = config.get("workspace")
            agent_id = config.get("agentId")
            rows.append(
                {
                    "id": project.get("id"),
                    "name": project.get("name"),
                    "workspace": workspace if isinstance(workspace, str) and workspace else None,
                    "default_agent": agent_id if isinstance(agent_id, str) and agent_id else None,
                }
            )
    return {"object": "list", "data": rows}


def _session_path(session_id: str) -> str:
    return f"/v1/sessions/{quote(session_id, safe='')}"


async def _call_tool(
    request: Request,
    auth_provider: AuthProvider | None,
    name: str,
    args: dict[str, Any],
) -> Any:
    api = _Api(request, auth_provider)
    if name == "health":
        return await api.call("GET", "/health")
    if name == "info":
        return await api.call("GET", "/v1/info")
    if name == "list_agents":
        return await api.call(
            "GET",
            _with_query(
                "/v1/agents",
                {
                    "limit": _optional_int(args.get("limit"), "limit", 1, 1000),
                    "after": _optional_str(args.get("after"), "after"),
                    "scope": _one_of(args.get("scope"), "scope", ("user",)),
                },
            ),
        )
    if name == "list_hosts":
        return await api.call("GET", "/v1/hosts")
    if name == "list_harnesses":
        return await api.call("GET", "/v1/harnesses")
    if name == "list_projects":
        return _present_projects(await api.call("GET", "/v1/projects"))
    if name == "list_sessions":
        project = await _project_query(api, args)
        visibility = _one_of(
            args.get("visibility"), "visibility", ("all", "mine", "shared", "archived")
        )
        include_archived = args.get("include_archived")
        if include_archived is not None and not isinstance(include_archived, bool):
            raise ToolFailure("include_archived must be a boolean")
        page = await api.call(
            "GET",
            _with_query(
                "/v1/sessions",
                {
                    "limit": _optional_int(args.get("limit"), "limit", 1, 1000),
                    "after": _optional_str(args.get("after"), "after"),
                    "before": _optional_str(args.get("before"), "before"),
                    "agent_id": _optional_str(args.get("agent_id"), "agent_id"),
                    "agent_name": _optional_str(args.get("agent_name"), "agent_name"),
                    "search_query": _optional_str(args.get("search_query"), "search_query"),
                    "include_archived": include_archived,
                    "order": _one_of(args.get("order"), "order", ("asc", "desc")),
                    "sort_by": _one_of(
                        args.get("sort_by"), "sort_by", ("created_at", "updated_at")
                    ),
                    "project": project,
                    "kind": _one_of(args.get("kind"), "kind", ("default", "sub_agent", "any")),
                    "visibility": visibility or "all",
                },
            ),
        )
        return _compact_sessions(page)
    if name == "get_session":
        session_id = _require_id(
            _require_present_str(args.get("session_id"), "session_id"), "session_id"
        )
        return _present_session(await api.call("GET", _session_path(session_id)))
    if name == "get_session_items":
        session_id = _require_id(
            _require_present_str(args.get("session_id"), "session_id"), "session_id"
        )
        max_chars = _optional_int(args.get("max_chars"), "max_chars", 200, 20_000) or 2000
        page = await api.call(
            "GET",
            _with_query(
                f"{_session_path(session_id)}/items",
                {
                    "limit": _optional_int(args.get("limit"), "limit", 1, 200) or 30,
                    "after": _optional_str(args.get("after"), "after"),
                    "before": _optional_str(args.get("before"), "before"),
                    "order": _one_of(args.get("order"), "order", ("asc", "desc")) or "desc",
                },
            ),
        )
        return _present_items(page, max_chars)
    if name == "create_session":
        body = await _build_create_body(api, args)
        if args.get("confirm") is not True:
            return _preview("POST", "/v1/sessions", body)
        created = await api.call("POST", "/v1/sessions", body)
        return {"session": _present_session(created)}
    if name == "send_message":
        return await _send_message(api, args)
    if name == "interrupt_turn":
        return await _control(api, args, "interrupt")
    if name == "stop_session":
        return await _control(api, args, "stop_session")
    from omnigent.server.mcp_extensions import NOT_AN_EXTENSION, call_extension

    extended = await call_extension(api, name, args)
    if extended is not NOT_AN_EXTENSION:
        return extended
    raise ToolFailure(f"Unknown tool: {name}")


def _require_present_str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ToolFailure(f"{name} is required")
    return value


async def _project_query(api: _Api, args: Mapping[str, Any]) -> str | None:
    """Resolve a project id from ``list_projects`` to the name ``GET /v1/sessions`` filters on.

    The sessions route filters by project name. Callers that copied an id from
    ``list_projects`` still match. An empty string lists sessions that are not
    filed in a project.
    """
    project = _optional_str(args.get("project"), "project")
    project_id = _optional_str(args.get("project_id"), "project_id")
    if project is not None and project_id is not None and project != project_id:
        raise ToolFailure("project and project_id disagree")
    chosen = project_id if project_id is not None else project
    if chosen is None or chosen == "":
        return chosen
    try:
        listed = await api.call("GET", "/v1/projects")
    except ToolFailure:
        return chosen
    data = listed.get("data") if isinstance(listed, dict) else None
    if not isinstance(data, list):
        return chosen
    for row in data:
        if isinstance(row, dict) and row.get("id") == chosen and isinstance(row.get("name"), str):
            return row["name"]
    return chosen


def _named_agent(args: Mapping[str, Any]) -> str | None:
    agent_name = _blank_to_none(_optional_str(args.get("agent_name"), "agent_name"))
    name = _blank_to_none(_optional_str(args.get("name"), "name"))
    if agent_name and name and agent_name != name:
        raise ToolFailure(f"agent_name and name disagree. {_AGENT_FIELD_HINT}.")
    return agent_name or name


def _same_name(agent: Any, wanted: str, *, loose: bool) -> bool:
    if not isinstance(agent, dict) or not isinstance(agent.get("name"), str):
        return False
    return agent["name"].lower() == wanted.lower() if loose else agent["name"] == wanted


async def _list_all_agents(api: _Api, scope: str | None) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    after: str | None = None
    for _page in range(20):
        body = await api.call(
            "GET", _with_query("/v1/agents", {"limit": 1000, "after": after, "scope": scope})
        )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            break
        agents.extend(item for item in data if isinstance(item, dict))
        last_id = body.get("last_id") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or not body.get("has_more")
            or not isinstance(last_id, str)
            or last_id == after
        ):
            break
        after = last_id
    return agents


async def _collect_agents(api: _Api) -> list[dict[str, Any]]:
    server_agents = await _list_all_agents(api, None)
    try:
        user_agents = await _list_all_agents(api, "user")
    except ToolFailure:
        user_agents = []
    seen: set[str] = set()
    agents: list[dict[str, Any]] = []
    for agent in [*server_agents, *user_agents]:
        agent_id = agent.get("id")
        if isinstance(agent_id, str) and agent_id in seen:
            continue
        if isinstance(agent_id, str):
            seen.add(agent_id)
        agents.append(agent)
    return agents


async def _resolve_agent_id(api: _Api, args: Mapping[str, Any]) -> str:
    explicit = _optional_str(args.get("agent_id"), "agent_id")
    if explicit:
        return _require_id(explicit, "agent_id")
    wanted = _named_agent(args)
    if not wanted:
        raise ToolFailure(f"create_session needs agent_id or agent_name. {_AGENT_FIELD_HINT}.")
    agents = await _collect_agents(api)
    exact = [agent for agent in agents if _same_name(agent, wanted, loose=False)]
    matches = exact or [agent for agent in agents if _same_name(agent, wanted, loose=True)]
    if not matches:
        raise ToolFailure(f"No agent named {wanted!r}. {_AGENT_FIELD_HINT}.")
    if len(matches) > 1:
        listed = ", ".join(
            f"{agent.get('id')} ({agent['harness']})"
            if agent.get("harness")
            else str(agent.get("id"))
            for agent in matches
        )
        raise ToolFailure(
            f"Several agents are named {wanted!r}. Pass agent_id. "
            f"{_AGENT_FIELD_HINT}. Matches: {listed}"
        )
    agent_id = matches[0].get("id")
    if not isinstance(agent_id, str) or not agent_id:
        raise ToolFailure(f"Agent {wanted!r} has no id. {_AGENT_FIELD_HINT}.")
    return _require_id(agent_id, "agent_id")


async def _build_create_body(api: _Api, args: Mapping[str, Any]) -> dict[str, Any]:
    agent_id = await _resolve_agent_id(api, args)
    host_type = _one_of(args.get("host_type"), "host_type", ("external", "managed"))
    host_id = _optional_str(args.get("host_id"), "host_id")
    workspace = _optional_str(args.get("workspace"), "workspace")
    title = _optional_str(args.get("title"), "title")
    message = args.get("message")
    if message is not None:
        message = _require_text(message)
    if host_type == "managed" and host_id:
        raise ToolFailure("host_id must be omitted when host_type is managed")
    if host_id and not workspace:
        raise ToolFailure("workspace is required when host_id is set")
    body: dict[str, Any] = {"agent_id": agent_id}
    if host_type:
        body["host_type"] = host_type
    if host_id:
        body["host_id"] = host_id
    if workspace:
        body["workspace"] = workspace
    if title:
        body["title"] = title
    if message:
        body["initial_items"] = [_user_message_event(message)]
    return body


async def _control(api: _Api, args: Mapping[str, Any], event_type: str) -> Any:
    session_id = _require_id(
        _require_present_str(args.get("session_id"), "session_id"), "session_id"
    )
    path = f"{_session_path(session_id)}/events"
    body = {"type": event_type, "data": {}}
    if args.get("confirm") is not True:
        return _preview("POST", path, body)
    return await api.call("POST", path, body)


async def _send_message(api: _Api, args: Mapping[str, Any]) -> Any:
    session_id = _require_id(
        _require_present_str(args.get("session_id"), "session_id"), "session_id"
    )
    text = _require_text(args.get("text"))
    body = _user_message_event(text)
    path = f"{_session_path(session_id)}/events"
    wait = args.get("wait") is True
    if args.get("confirm") is not True:
        extra = None
        if wait:
            extra = {
                "wait": {
                    "stream": f"GET {_session_path(session_id)}/stream?idle=false",
                    "timeout_seconds": _timeout_seconds(args.get("timeout_seconds")),
                }
            }
        return _preview("POST", path, body, extra)
    if not wait:
        return await api.call("POST", path, body)
    result = await _watch_turn(
        api, session_id, body, _timeout_seconds(args.get("timeout_seconds"))
    )
    if not result["reply"] and not result["needs_input"]:
        try:
            page = await api.call(
                "GET",
                _with_query(f"{_session_path(session_id)}/items", {"limit": 30, "order": "desc"}),
            )
        except ToolFailure:
            page = None
        recovered = _reply_after_user(page, text)
        if recovered:
            result["reply"] = recovered
            result["reply_source"] = "items"
    return result


async def _wait_for_client(request: Request, waiter: asyncio.Future[Any], timeout: float) -> Any:
    """Return ``waiter``'s result, or raise when the MCP client disconnects.

    A finished waiter returns immediately. Disconnect is polled about four
    times a second so a hung turn does not keep the handler open after the
    client has gone. This does not cancel ``waiter``.

    :param request: The ``/mcp`` request. ``is_disconnected`` is the ASGI signal.
    :param waiter: The stream-open task or the live-event pump.
    :param timeout: Seconds to wait before :class:`TimeoutError`.
    :returns: Whatever ``waiter`` completed with.
    :raises ToolFailure: The client disconnected before ``waiter`` finished.
    :raises TimeoutError: ``timeout`` elapsed first.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if waiter.done():
            return waiter.result()
        if await request.is_disconnected():
            raise ToolFailure("client disconnected")
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        await asyncio.wait({waiter}, timeout=min(_DISCONNECT_SLICE_S, remaining))


async def _watch_posted(
    api: _Api, session_id: str, post: Callable[[], Awaitable[Any]], budget_s: float
) -> dict[str, Any]:
    """Read-check the session, subscribe, run ``post``, then read until the turn ends.

    The read is ``GET /v1/sessions/{id}?include_items=false``. That route
    applies the same read permission as ``GET .../stream``, and it runs
    before subscribe so a refused caller never joins the live stream.
    """
    request = api._request
    if await request.is_disconnected():
        raise ToolFailure("client disconnected")
    await api.call("GET", _with_query(_session_path(session_id), {"include_items": False}))
    if await request.is_disconnected():
        raise ToolFailure("client disconnected")
    state = _TurnState()
    ready = asyncio.Event()
    task = asyncio.create_task(_pump_live_events(session_id, state, ready))
    accepted: Any = None
    timed_out = False
    post_error: ToolFailure | None = None
    try:
        ready_wait = asyncio.create_task(ready.wait())
        try:
            try:
                await _wait_for_client(request, ready_wait, 5)
            except TimeoutError as exc:
                raise ToolFailure("timed out opening the session live stream") from exc
        finally:
            if not ready_wait.done():
                ready_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ready_wait
        try:
            accepted = await post()
        except ToolFailure as exc:
            post_error = exc
        else:
            try:
                await _wait_for_client(request, task, budget_s)
            except TimeoutError:
                timed_out = not state.done
                if not state.done:
                    state.finish("timeout")
            except asyncio.CancelledError:
                raise
            except ToolFailure:
                raise
            except Exception as exc:  # noqa: BLE001 — a dropped live tail still returns what was observed
                _logger.debug("mcp live stream ended for %s: %s", session_id, exc)
                if not state.done:
                    state.end_reason = state.end_reason or "stream_closed"
            else:
                if not state.done:
                    state.end_reason = state.end_reason or "stream_closed"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if post_error is not None:
        raise post_error
    return state.to_result(accepted, timed_out)


async def _watch_turn(
    api: _Api, session_id: str, body: dict[str, Any], budget_s: float
) -> dict[str, Any]:
    """Read-check the session, subscribe, post the message, then read until the turn ends."""

    async def post() -> Any:
        return await api.call("POST", f"{_session_path(session_id)}/events", body)

    return await _watch_posted(api, session_id, post, budget_s)


async def _pump_live_events(session_id: str, state: _TurnState, ready: asyncio.Event) -> None:
    from omnigent.runtime import session_stream

    events = session_stream.subscribe(session_id, ready_event={"type": "session.heartbeat"})
    try:
        ready_seen = False
        async for event in events:
            if not isinstance(event, dict):
                continue
            if not ready_seen and event.get("type") == "session.heartbeat":
                ready_seen = True
                ready.set()
                continue
            state.observe(event)
            if state.done:
                return
    finally:
        # subscribe() is an async generator; close it so the slot is unregistered.
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()


def _text_from_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _reply_after_user(page: Any, user_text: str) -> str:
    data = page.get("data") if isinstance(page, dict) else None
    if not isinstance(data, list):
        return ""
    seen = False
    reply = ""
    for item in reversed(data):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        data_field = item.get("data") if isinstance(item.get("data"), dict) else {}
        role = item.get("role") or data_field.get("role")
        text = _text_from_content(item.get("content") or data_field.get("content"))
        if role == "user" and text == user_text:
            seen = True
            reply = ""
            continue
        if seen and role == "assistant" and text:
            reply = text
    return reply if seen else ""


def _flatten_event(event: Any) -> Any:
    if not isinstance(event, dict):
        return event
    data = event.get("data")
    if not isinstance(data, dict):
        return event
    return {**data, **event, "type": event.get("type") or data.get("type")}


class _TurnState:
    """Fold live-stream events into one turn outcome.

    A connect-time ``idle`` from before this message, and a status flap that
    carries no response id before the turn has produced anything, are not the
    end of the turn. An elicitation ends the wait as ``needs_input``.
    """

    def __init__(self) -> None:
        self.turn_started = False
        self.turn_produced = False
        self.open_response_id: str | None = None
        self.saw_open_running = False
        self.buckets: dict[str, str] = {}
        self.last_index: dict[str, int] = {}
        self.status: str | None = None
        self.done = False
        self.end_reason: str | None = None
        self.needs_input: dict[str, Any] | None = None
        self.error: Any = None
        self.event_count = 0

    def reply_text(self) -> str:
        return "".join(self.buckets.values())

    def finish(self, reason: str) -> None:
        if self.done:
            return
        self.done = True
        self.end_reason = reason

    def _add_delta(self, event: Mapping[str, Any]) -> None:
        message_id = event.get("message_id")
        bucket_id = message_id if isinstance(message_id, str) else ""
        delta = event.get("delta")
        if not isinstance(delta, str) or not delta:
            return
        index = event.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            previous = self.last_index.get(bucket_id)
            if previous is not None and index <= previous:
                return
            self.last_index[bucket_id] = index
        so_far = self.buckets.get(bucket_id, "")
        self.buckets[bucket_id] = delta if so_far and delta.startswith(so_far) else so_far + delta

    def _capture_completed(self, event: Mapping[str, Any]) -> None:
        if self.reply_text():
            return
        response = event.get("response")
        output = response.get("output") if isinstance(response, dict) else None
        if not isinstance(output, list):
            return
        text = "".join(
            _text_from_content(item.get("content") if isinstance(item, dict) else None)
            for item in output
        )
        if text:
            self.buckets[""] = text

    def observe(self, raw: Any) -> None:
        if self.done:
            return
        event = _flatten_event(raw)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return
        self.event_count += 1
        event_type = event["type"]
        if event_type == "[DONE]":
            if self.turn_started:
                self.finish("done")
            return
        if event_type == "response.output_text.delta":
            self._add_delta(event)
            self.turn_started = True
            self.turn_produced = True
        elif event_type in {"response.completed", "response.incomplete"}:
            self.turn_produced = True
            self._capture_completed(event)
        elif event_type in {"session.input.consumed", "response.created", "response.in_progress"}:
            self.turn_started = True
        elif event_type == "response.elicitation_request":
            self.turn_started = True
            self.status = self.status or "waiting"
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            self.needs_input = {
                "elicitation_id": event.get("elicitation_id"),
                "message": params.get("message")
                if params.get("message") is not None
                else event.get("message"),
            }
            self.finish("elicitation")
            return
        if event_type in _HARD_TERMINAL:
            detail = None
            response = event.get("response")
            if isinstance(response, dict):
                detail = response.get("error")
            if detail is None:
                detail = event.get("error")
            if isinstance(detail, str):
                self.error = detail
            elif isinstance(detail, dict) and detail.get("message"):
                self.error = detail.get("message")
            else:
                self.error = event_type
            if event_type in {"response.failed", "turn.failed"}:
                self.status = "failed"
            if event_type in {"response.cancelled", "turn.cancelled"}:
                self.status = "idle"
            self.finish(event_type)
            return
        if event_type != "session.status" or not isinstance(event.get("status"), str):
            return
        status = event["status"]
        response_id = event.get("response_id")
        response_id = response_id if isinstance(response_id, str) and response_id else None
        if status in {"running", "waiting", "launching"}:
            self.turn_started = True
            self.status = status
            if response_id:
                self.open_response_id = response_id
                self.saw_open_running = True
            return
        if status not in {"idle", "failed"} or not self.turn_started:
            return
        id_match = bool(response_id) and (
            not self.saw_open_running or response_id == self.open_response_id
        )
        id_less_end = (
            not response_id
            and not self.saw_open_running
            and (self.turn_produced or status == "failed")
        )
        if id_match or id_less_end:
            self.status = status
            if status == "failed":
                self.error = event.get("error") if event.get("error") is not None else self.error
            self.finish(status)

    def to_result(self, accepted: Any, timed_out: bool) -> dict[str, Any]:
        return {
            "accepted": accepted,
            "status": self.status,
            "reply": self.reply_text(),
            "reply_source": "stream",
            "timed_out": timed_out,
            "needs_input": self.needs_input,
            "error": self.error,
            "end_reason": "timeout" if timed_out else self.end_reason,
            "event_count": self.event_count,
        }
