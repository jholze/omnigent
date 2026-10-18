"""``outputSchema`` documents for the ``chat_*`` MCP tools.

Each schema is a ``oneOf`` of success, an optional dry-run preview, and the
stable error object. They are advertised only for protocol versions
2025-06-18 and newer.
"""

from __future__ import annotations

from typing import Any

_MESSAGE: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "channel_id": {"type": "string"},
        "thread_root_id": {"type": ["string", "null"]},
        "message_id": {"type": "string"},
        "created_at": {"type": "integer"},
        "author": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["human", "agent_account", "omnigent_agent", "system"],
                },
                "display": {"type": "string"},
            },
            "required": ["id", "kind", "display"],
        },
        "body": {"type": "string"},
        "content_trust": {"type": "string", "enum": ["untrusted"]},
    },
    "required": ["channel_id", "message_id", "author", "body", "content_trust"],
}

_ERROR: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "error": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "code": {"type": "string"},
                "http_status": {"type": "integer"},
                "message": {"type": "string"},
                "retry_after_s": {"type": "integer"},
            },
            "required": ["code", "http_status", "message"],
        }
    },
    "required": ["error"],
}

_PREVIEW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "preview": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "method": {"type": "string"},
                "path": {"type": "string"},
                "body": {},
            },
            "required": ["method", "path"],
        }
    },
    "required": ["preview"],
}


def _one_of(*branches: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": list(branches)}


def _success(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
        "required": required,
    }


def output_schema(name: str) -> dict[str, Any]:
    """Return the ``outputSchema`` for one ``chat_*`` tool."""
    success = _SUCCESS[name]
    if name in _WRITE_TOOLS:
        return _one_of(success, _PREVIEW, _ERROR)
    return _one_of(success, _ERROR)


_WRITE_TOOLS = frozenset({"chat_post", "chat_open_dm", "chat_mark_read"})

_SUCCESS: dict[str, dict[str, Any]] = {
    "chat_list_channels": _success(
        {
            "channels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string"},
                        "name": {"type": ["string", "null"]},
                        "unread_count": {"type": "integer"},
                        "mention_count": {"type": "integer"},
                    },
                },
            }
        },
        ["channels"],
    ),
    "chat_read": _success(
        {
            "items": {"type": "array", "items": _MESSAGE},
            "has_more": {"type": "boolean"},
            "next_after": {"type": ["string", "null"]},
        },
        ["items"],
    ),
    "chat_post": _success(
        {
            "message": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "thread_root_id": {"type": ["string", "null"]},
                    "created_at": {"type": "integer"},
                },
            },
            "deduplicated": {"type": "boolean"},
        },
        ["message"],
    ),
    "chat_open_dm": _success(
        {"dm": {"type": "object", "properties": {"id": {"type": "string"}}}},
        ["dm"],
    ),
    "chat_list_members": _success(
        {
            "members": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string"},
                        "display": {"type": "string"},
                        "status": {"type": ["string", "null"]},
                    },
                },
            }
        },
        ["members"],
    ),
    "chat_unread": _success(
        {
            "items": {"type": "array", "items": _MESSAGE},
            "next_cursor": {"type": ["string", "null"]},
        },
        ["items"],
    ),
    "chat_mark_read": _success(
        {
            "channel_id": {"type": "string"},
            "thread_root_id": {"type": "string"},
            "cursor": {"type": "object"},
        },
        ["channel_id"],
    ),
}
