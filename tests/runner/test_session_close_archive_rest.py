"""
Runner REST-path tests: close of standalone children + ``sys_session_archive``.

Regression for the prod bug where a hub's ``sys_session_close`` on a child
created by ``sys_session_create(agent_id=...)`` returned
``session_not_a_sub_agent``: the child keeps a verbatim title (no
``"<agent>:<title>"`` colon form) and the close path required that form.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from omnigent.runner.tool_dispatch import _execute_session_query_tool
from omnigent.util.session_lifecycle import (
    ARCHIVED_BY_PARENT_LABEL_KEY,
    CLOSED_LABEL_KEY,
    CLOSED_LABEL_VALUE,
)

HUB = "conv_hub"
CHILD = "conv_child"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://server")


def _snap(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": CHILD,
        "title": "X | coder | smoke",
        "kind": "sub_agent",
        "sub_agent_name": None,
        "agent_name": "equent-coder",
        "root_conversation_id": HUB,
        "parent_session_id": HUB,
        "status": "idle",
        "archived": False,
        "labels": {},
        "background_task_count": 0,
        "pending_elicitations": [],
        "pending_inputs": [],
        "active_response_id": None,
    }
    base.update(overrides)
    return base


def _handler(snap: dict[str, Any], patches: list[dict[str, Any]], patch_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == f"/v1/sessions/{snap['id']}":
            return httpx.Response(200, json=snap)
        if request.method == "GET" and request.url.path == f"/v1/sessions/{HUB}":
            return httpx.Response(200, json={"id": HUB, "root_conversation_id": HUB})
        if request.method == "PATCH" and request.url.path == f"/v1/sessions/{snap['id']}":
            patches.append(json.loads(request.content))
            return httpx.Response(patch_status, json={"id": snap["id"]})
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    return handler


async def _call(tool: str, snap: dict[str, Any], patches: list[dict[str, Any]], **kw: Any):
    async with _client(_handler(snap, patches, **kw)) as client:
        return json.loads(
            await _execute_session_query_tool(
                tool,
                json.dumps({"conversation_id": snap["id"]}),
                conversation_id=HUB,
                server_client=client,
            )
        )


@pytest.mark.asyncio
async def test_close_standalone_child_succeeds() -> None:
    """AC-1: verbatim-titled standalone child is closed, not rejected."""
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_close", _snap(), patches)
    assert out == {
        "closed": True,
        "conversation_id": CHILD,
        "agent": "equent-coder",
        "title": "X | coder | smoke",
    }
    assert patches == [
        {
            "title": f"X | coder | smoke:closed:{CHILD}",
            "labels": {CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE},
            "archived": True,
        }
    ]


@pytest.mark.asyncio
async def test_close_named_child_unchanged() -> None:
    """AC-5 regression: named children keep the canonical tombstone."""
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_close", _snap(title="researcher:auth"), patches)
    assert out["agent"] == "researcher" and out["title"] == "auth"
    assert patches[0]["title"] == f"researcher:auth:closed:{CHILD}"


@pytest.mark.asyncio
async def test_close_prefers_sub_agent_name_for_identity() -> None:
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_close", _snap(sub_agent_name="coder"), patches)
    assert out["agent"] == "coder"


@pytest.mark.asyncio
async def test_archive_ended_direct_child(caplog: pytest.LogCaptureFixture) -> None:
    """AC-4: ended direct child is archived + closed + stamped; audited."""
    caplog.set_level(logging.INFO, logger="omnigent.audit.session_archive")
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(), patches)
    assert out == {
        "archived": True,
        "conversation_id": CHILD,
        "agent": "equent-coder",
        "title": "X | coder | smoke",
    }
    assert patches == [
        {
            "archived": True,
            "labels": {
                ARCHIVED_BY_PARENT_LABEL_KEY: HUB,
                CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE,
            },
            "title": f"X | coder | smoke:closed:{CHILD}",
        }
    ]
    # Never a DELETE: the handler would raise on any other verb.
    assert any(
        f"caller={HUB!r} target={CHILD!r} outcome='archived'" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_archive_already_closed_child_does_not_rewrite_title() -> None:
    patches: list[dict[str, Any]] = []
    snap = _snap(
        title=f"X | coder | smoke:closed:{CHILD}",
        labels={CLOSED_LABEL_KEY: CLOSED_LABEL_VALUE},
    )
    out = await _call("sys_session_archive", snap, patches)
    assert out["archived"] is True
    assert patches == [{"archived": True, "labels": {ARCHIVED_BY_PARENT_LABEL_KEY: HUB}}]


@pytest.mark.asyncio
async def test_archive_already_archived_is_noop() -> None:
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(archived=True), patches)
    assert out == {"archived": True, "already_archived": True, "conversation_id": CHILD}
    assert patches == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"status": "running"}, "status=running"),
        ({"status": "waiting"}, "status=waiting"),
        ({"active_response_id": "resp_1"}, "active_response"),
        ({"background_task_count": 2}, "background_tasks"),
        ({"pending_elicitations": [{"id": "e"}]}, "pending_input"),
    ],
)
async def test_archive_refuses_running_child(overrides: dict[str, Any], reason: str) -> None:
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(**overrides), patches)
    assert out["error"] == "session_running"
    assert out["reason"] == reason
    assert patches == []


@pytest.mark.asyncio
async def test_archive_refuses_in_flight_runner_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner-local sub-agent work still running blocks the archive."""
    from omnigent.runner import subagent_work

    class _Work:
        status = "running"

    monkeypatch.setattr(
        subagent_work, "get_subagent_work", lambda cid: _Work() if cid == CHILD else None
    )
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(), patches)
    assert out["error"] == "session_running" and out["reason"] == "work=running"
    assert patches == []


@pytest.mark.asyncio
async def test_archive_refuses_non_direct_child() -> None:
    """Grandchild in the same tree (parent != caller) is refused."""
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(parent_session_id="conv_other"), patches)
    assert out["error"] == "session_out_of_tree"
    assert patches == []


@pytest.mark.asyncio
async def test_archive_refuses_top_level() -> None:
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(parent_session_id=None), patches)
    assert out == {"error": "session_not_a_sub_agent", "conversation_id": CHILD}
    assert patches == []


@pytest.mark.asyncio
async def test_archive_maps_patch_forbidden() -> None:
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_archive", _snap(), patches, patch_status=403)
    assert out == {"error": "session_out_of_tree", "conversation_id": CHILD}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title,agent,label,stored",
    [
        ("Fix: auth", "Fix", " auth", f"Fix: auth:closed:{CHILD}"),
        ("ui:claude-native-ui:1", "claude-native-ui", "1", f"claude-native-ui:1:closed:{CHILD}"),
        (None, "equent-coder", "", f":closed:{CHILD}"),
    ],
)
async def test_close_title_shapes(title: Any, agent: str, label: str, stored: str) -> None:
    """Colon-containing verbatim, ui:-prefixed and missing titles all close."""
    patches: list[dict[str, Any]] = []
    out = await _call("sys_session_close", _snap(title=title), patches)
    assert out["agent"] == agent and out["title"] == label
    assert patches[0]["title"] == stored


@pytest.mark.asyncio
async def test_archive_refusal_is_audited_with_quoted_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="omnigent.audit.session_archive")
    patches: list[dict[str, Any]] = []
    await _call("sys_session_archive", _snap(id="conv_x\nforged", parent_session_id="o"), patches)
    messages = [r.getMessage() for r in caplog.records]
    # Exactly one audit record, with the model-supplied newline escaped.
    assert len(messages) == 1
    assert "target='conv_x\\nforged'" in messages[0]
    assert "\n" not in messages[0]
