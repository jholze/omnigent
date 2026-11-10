"""Stall auto-interrupt must not drop a buffered follow-up.

Owner Stop on claude-native pops the runner buffer. SDK and codex-native
keep it so the next turn can start. The stall watchdog therefore marks
claude-native only, and uses the real interrupt path for SDK.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response

from omnigent.harnesses.claude_native import bridge as claude_native_bridge
from omnigent.runner import app as runner_app
from omnigent.runner import create_runner_app
from omnigent.runner.native.interrupt import NativeInterruptRunner
from omnigent.spec.types import AgentSpec, ExecutorSpec
from tests.runner.conftest import (
    _FakeProcessManager,
    _runner_client,
    _ScriptedHarnessClient,
    _sse,
)
from tests.runner.helpers import NullServerClient


def _build_app(harness_name: str) -> tuple[FastAPI, _ScriptedHarnessClient]:
    spec = AgentSpec(
        spec_version=1,
        name="t",
        executor=ExecutorSpec(type="omnigent", config={"harness": harness_name}),
    )

    async def resolver(agent_id: str, session_id: str | None = None) -> AgentSpec:
        del agent_id, session_id
        return spec

    harness = _ScriptedHarnessClient(
        [
            _sse({"type": "response.created", "response": {"id": "resp_next"}}),
            _sse({"type": "response.completed", "response": {"id": "resp_next"}}),
        ]
    )
    app = create_runner_app(
        process_manager=_FakeProcessManager(harness),  # type: ignore[arg-type]
        spec_resolver=resolver,
        server_client=NullServerClient(),  # type: ignore[arg-type]
    )
    return app, harness


def _message(text: str) -> dict[str, Any]:
    return {"type": "message", "content": [{"type": "input_text", "text": text}]}


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


async def _hang() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_sdk_interrupt_keeps_buffer_and_starts_next_turn() -> None:
    """A real SDK interrupt leaves the follow-up so the next turn can start."""
    app, harness = _build_app("claude-sdk")
    sid = uuid.uuid4().hex
    async with _runner_client(app) as client:
        created = await client.post(
            "/v1/sessions", json={"session_id": sid, "agent_id": uuid.uuid4().hex}
        )
        assert created.status_code == 201
        stuck = asyncio.create_task(_hang(), name=f"stuck-{sid}")
        app.state.active_turns[sid] = stuck
        app.state.session_message_buffers[sid] = [_message("follow-up")]
        interrupt = await client.post(f"/v1/sessions/{sid}/events", json={"type": "interrupt"})
        assert interrupt.status_code == 204
        await _until(
            lambda: (
                sid not in app.state.active_turns or app.state.active_turns.get(sid) is not stuck
            )
        )
        await _until(
            lambda: bool(harness.posted_bodies) or not app.state.session_message_buffers.get(sid)
        )
        assert harness.posted_bodies, "buffered follow-up must start the next turn"
        body = harness.posted_bodies[0]
        texts = []
        for item in body.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and "text" in part:
                        texts.append(part["text"])
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
        assert any("follow-up" in str(text) for text in texts) or "follow-up" in str(body)


@pytest.mark.asyncio
async def test_claude_native_interrupt_clears_the_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner Stop on claude-native still drops queued input (stall is mark-only)."""
    app, harness = _build_app("claude-native")
    sid = uuid.uuid4().hex
    monkeypatch.setattr(claude_native_bridge, "inject_interrupt", lambda *_a, **_k: None)

    async def skip_terminal_launch(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def bridge_id(**_: Any) -> str:
        return "stall-bridge"

    monkeypatch.setattr(runner_app, "_launch_native_terminal", skip_terminal_launch)
    monkeypatch.setattr(
        "omnigent.runner.native.interrupt._claude_native_bridge_id_for_session",
        bridge_id,
    )
    async with _runner_client(app) as client:
        await client.post("/v1/sessions", json={"session_id": sid, "agent_id": uuid.uuid4().hex})
        app.state.active_turns[sid] = None
        app.state.session_message_buffers[sid] = [_message("follow-up")]
        interrupt = await client.post(f"/v1/sessions/{sid}/events", json={"type": "interrupt"})
        assert interrupt.status_code == 204
        assert sid not in app.state.session_message_buffers
        assert harness.posted_bodies == []


@pytest.mark.asyncio
async def test_codex_native_interrupt_keeps_the_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex-native interrupt does not pop the runner follow-up buffer."""
    app, harness = _build_app("codex-native")
    sid = uuid.uuid4().hex

    async def _codex_interrupt(self: NativeInterruptRunner, conv_id: str) -> Response:
        del self, conv_id
        return Response(status_code=204)

    monkeypatch.setattr(NativeInterruptRunner, "_codex_interrupt", _codex_interrupt)
    async with _runner_client(app) as client:
        await client.post("/v1/sessions", json={"session_id": sid, "agent_id": uuid.uuid4().hex})
        app.state.active_turns[sid] = None
        app.state.session_message_buffers[sid] = [_message("follow-up")]
        interrupt = await client.post(f"/v1/sessions/{sid}/events", json={"type": "interrupt"})
        assert interrupt.status_code == 204
        buffered = app.state.session_message_buffers.get(sid)
        assert buffered
        assert buffered[0]["content"][0]["text"] == "follow-up"
        assert harness.posted_bodies == []
