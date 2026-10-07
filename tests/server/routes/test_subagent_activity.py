"""Lifecycle notices remain named, ordered, and idempotent across native delivery races."""

import json
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy import event

from omnigent.entities import MessageData, NewConversationItem
from omnigent.server.routes._sessions.orchestration import (
    _persist_external_conversation_item,
    _persist_external_conversation_items,
)
from omnigent.server.schemas import SessionEventInput
from omnigent.server.subagent_activity import (
    CLAUDE_SUBAGENT_OUTCOME_LABEL,
    record_subagent_activity,
)
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


@pytest.mark.parametrize("explicit_turn", [False, True])
@pytest.mark.asyncio
async def test_lifecycle_publishes_once_per_child_turn(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, explicit_turn: bool
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(parent_conversation_id=parent.id, title="researcher:Audit")
    publish = Mock()
    monkeypatch.setattr("omnigent.server.subagent_activity.session_stream.publish", publish)
    await record_subagent_activity(child.id, "delegated", store, parent_id="wrong-parent")
    await record_subagent_activity(child.id, "returned", store)
    assert store.list_items(parent.id).data == []
    for _ in range(2):
        await record_subagent_activity(child.id, "delegated", store)
    for turn_id in ("first", "second"):
        store.append(
            child.id,
            [
                NewConversationItem(
                    type="message",
                    response_id=turn_id,
                    data=MessageData(
                        role="assistant",
                        agent="Claude",
                        content=[{"type": "output_text", "text": "Done"}],
                    ),
                )
            ],
        )
        for _ in range(2):
            await record_subagent_activity(
                child.id, "returned", store, turn_id=turn_id if explicit_turn else None
            )
    items = store.list_items(parent.id).data
    assert [item.data.event_type for item in items] == [
        "session.subagent.delegated",
        "session.subagent.returned",
        "session.subagent.returned",
    ]
    assert publish.call_count == 3
    for call, item in zip(publish.call_args_list, items, strict=True):
        assert call.args[0] == parent.id
        assert call.args[1]["type"] == "response.output_item.done"
        assert call.args[1]["item"]["id"] == item.id
        assert item.data.resource_id == child.id
        assert item.data.resource == {"title": "Audit"}


@pytest.mark.asyncio
async def test_side_chat_child_records_no_lifecycle_notices(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        parent_conversation_id=parent.id,
        labels={"omnigent.codex_native.agent_nickname": "Side chat"},
    )
    publish = Mock()
    monkeypatch.setattr("omnigent.server.subagent_activity.session_stream.publish", publish)
    await record_subagent_activity(child.id, "delegated", store)
    await record_subagent_activity(child.id, "returned", store, turn_id="t1")
    assert store.list_items(parent.id).data == []
    publish.assert_not_called()


def _message(text: str) -> dict[str, Any]:
    return {"role": "user", "is_meta": True, "content": [{"type": "input_text", "text": text}]}


@pytest.mark.parametrize(
    "late,batched", [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize(
    "data,return_id,expected",
    [
        ({"call_id": "tool-1", "output": "Done"}, "agent-1", "completed"),
        (_message('<agent-message from="reviewer">Done</agent-message>'), "agent-1", "completed"),
        (
            _message(
                "<task-notification><task-id>agent-1</task-id><status>completed</status>"
                "</task-notification>"
            ),
            None,
            "completed",
        ),
        (
            _message(
                "<task-notification><tool-use-id>tool-1</tool-use-id><status>failed</status>"
                "</task-notification>"
            ),
            None,
            "failed",
        ),
        (
            _message(
                "<task-notification><task-id>agent-1</task-id><status>killed</status>"
                "</task-notification>"
            ),
            None,
            "cancelled",
        ),
        (
            {"call_id": "tool-1", "output": "Async agent launched successfully. agentId: agent-1"},
            None,
            None,
        ),
        ({"call_id": "tool-1", "output": '{"status":"failed","agentId":"agent-1"}'}, None, None),
        (_message('<teammate-message teammate_id="reviewer">Hi</teammate-message>'), None, None),
    ],
    ids=[
        "tool",
        "handback",
        "notification",
        "failure",
        "cancelled",
        "launch",
        "unconfirmed",
        "chatter",
    ],
)
@pytest.mark.asyncio
async def test_claude_completion_survives_retries_and_late_child_discovery(
    db_uri: str,
    data: dict[str, Any],
    return_id: str | None,
    expected: str | None,
    late: bool,
    batched: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    publish_status = Mock()
    monkeypatch.setattr("omnigent.server.routes._sessions.helpers._publish_status", publish_status)

    async def register_child() -> str:
        child = store.create_conversation(
            parent_conversation_id=parent.id, title="Explore:agent-1"
        )
        store.set_labels(
            child.id,
            {
                "omnigent.wrapper": "claude-code-native-ui-subagent",
                "omnigent.claude_native.subagent_id": "agent-1",
                "omnigent.claude_native.tool_use_id": "tool-1",
                "omnigent.claude_native.description": "Inspect authentication",
            },
        )
        await record_subagent_activity(child.id, "delegated", store)
        return child.id

    child_id = None if late else await register_child()
    item_type = "function_call_output" if "call_id" in data else "message"
    body = SessionEventInput(
        type="external_conversation_item",
        data={
            "source_id": "result",
            "response_id": "parent-turn",
            "item_type": item_type,
            "item_data": data,
            "subagent_return_id": return_id,
        },
    )

    async def deliver() -> None:
        if batched:
            await _persist_external_conversation_items(parent.id, [body], store)
        else:
            await _persist_external_conversation_item(parent.id, parent, body, store)

    if late and expected:

        def fail_marker_write(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT") and "session.subagent.completion-observed" in str(
                parameters
            ):
                raise RuntimeError("marker write failed")

        event.listen(store._conv_engine, "after_cursor_execute", fail_marker_write)
        try:
            with pytest.raises(RuntimeError, match="marker write failed"):
                await deliver()
        finally:
            event.remove(store._conv_engine, "after_cursor_execute", fail_marker_write)
        assert store.list_items(parent.id).data == []
    for _ in range(2):
        await deliver()
    [persisted] = store.list_items(parent.id, type=item_type).data
    assert persisted.data.subagent_return_id == return_id
    if late:
        # Reconciliation must work even when completion is outside the latest history page.
        store.append(
            parent.id,
            [
                NewConversationItem(
                    type="message",
                    response_id=f"update-{i}",
                    data=MessageData(
                        role="assistant",
                        agent="Claude",
                        content=[{"type": "output_text", "text": "Update"}],
                    ),
                )
                for i in range(101)
            ],
        )
        child_id = await register_child()
    assert child_id is not None
    await record_subagent_activity(child_id, "delegated", store)
    activity = [
        row
        for row in store.list_items(parent.id, type="resource_event").data
        if row.data.event_type != "session.subagent.completion-observed"
    ]
    assert [row.data.event_type for row in activity] == ["session.subagent.delegated"] + (
        ["session.subagent.returned"] if expected else []
    )
    assert all(row.data.resource_id == child_id for row in activity)
    assert activity[-1].data.resource == {
        "title": "Inspect authentication",
        **({"status": expected} if expected else {}),
    }
    child = store.get_conversation(child_id)
    assert child is not None
    assert child.labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == (expected or "")
    assert publish_status.call_args.args == (
        child_id,
        "running" if expected is None else "failed" if expected == "failed" else "idle",
    )
    if late and expected:
        assert all(call.args[1] != "running" for call in publish_status.call_args_list)


@pytest.mark.parametrize("tool_name", ["Agent", "Task", "SendMessage"])
@pytest.mark.asyncio
async def test_claude_resume_rejects_previous_invocation_completion(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        labels={
            "omnigent.wrapper": "claude-code-native-ui-subagent",
            "omnigent.claude_native.subagent_id": "agent-1",
            "omnigent.claude_native.tool_use_id": "tool-1",
        },
    )
    other_child = store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        labels={
            "omnigent.wrapper": "claude-code-native-ui-subagent",
            "omnigent.claude_native.subagent_id": "agent-other",
            CLAUDE_SUBAGENT_OUTCOME_LABEL: "completed",
        },
    )
    publish_status = Mock()
    monkeypatch.setattr("omnigent.server.routes._sessions.helpers._publish_status", publish_status)

    async def deliver(
        source: str, data: dict[str, Any], item_type: str, return_id: str | None = None
    ) -> None:
        await _persist_external_conversation_items(
            parent.id,
            [
                SessionEventInput(
                    type="external_conversation_item",
                    data={
                        "source_id": source,
                        "response_id": "parent-turn",
                        "item_type": item_type,
                        "item_data": data,
                        "subagent_return_id": return_id,
                    },
                )
            ],
            store,
        )

    async def complete(call_id: str) -> None:
        await deliver(
            call_id + "-result",
            {"call_id": call_id, "output": "Done"},
            "function_call_output",
            "agent-1",
        )

    await record_subagent_activity(child.id, "delegated", store)
    await complete("tool-1")
    old_handback = _message('<agent-message from="reviewer">Done</agent-message>')
    old_notification = _message(
        "<task-notification><task-id>agent-1</task-id>"
        "<status>completed</status></task-notification>"
    )
    await deliver("old-handback", old_handback, "message", "agent-1")
    await deliver("old-task-notification", old_notification, "message")
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"
    publish_status.reset_mock()
    await _persist_external_conversation_items(
        child.id,
        [
            SessionEventInput(
                type="external_conversation_item",
                data={
                    "source_id": "delayed-child-output",
                    "response_id": "child-turn",
                    "item_type": "message",
                    "item_data": {
                        "role": "assistant",
                        "agent": "Claude",
                        "content": [{"type": "output_text", "text": "Final result"}],
                    },
                },
            )
        ],
        store,
    )
    publish_status.assert_not_called()
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"

    resume = {
        "agent": "Claude",
        "name": tool_name,
        "call_id": "tool-2",
        "arguments": json.dumps(
            {
                "to": "agent-1",
                "summary": "Continue review",
                "message": "Continue",
                "type": "message",
                "recipient": "agent-1",
                "recipient_kind": "agent",
                "content": "Continue",
            }
            if tool_name == "SendMessage"
            else {"resume": "agent-1", "prompt": "Continue"}
        ),
    }
    with monkeypatch.context() as patch:
        patch.setattr(store, "set_labels", Mock(side_effect=RuntimeError("write failed")))
        await deliver("resume", resume, "function_call")
    publish_status.assert_not_called()
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"
    await deliver("resume", resume, "function_call")
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == ""
    assert store.get_conversation(other_child.id).labels == other_child.labels
    publish_status.assert_called_once_with(child.id, "running")
    publish_status.reset_mock()
    if tool_name == "SendMessage":
        await deliver(
            "send-ack", {"call_id": "tool-2", "output": "Message sent"}, "function_call_output"
        )
    await record_subagent_activity(child.id, "delegated", store)
    await complete("tool-1")
    await deliver("old-handback", old_handback, "message", "agent-1")
    await deliver("old-task-notification", old_notification, "message")
    await deliver(
        "old-notification",
        _message(
            "<task-notification><task-id>agent-1</task-id>"
            "<tool-use-id>tool-1</tool-use-id><status>completed</status></task-notification>"
        ),
        "message",
    )
    publish_status.assert_not_called()
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == ""
    if tool_name == "SendMessage":
        await deliver(
            "continued-result",
            _message(
                "<task-notification><task-id>agent-1</task-id>"
                "<tool-use-id>tool-2</tool-use-id><status>completed</status></task-notification>"
            ),
            "message",
        )
    else:
        await complete("tool-2")
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"
    publish_status.reset_mock()
    await deliver("resume", resume, "function_call")
    publish_status.assert_not_called()
    assert store.get_conversation(child.id).labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"


@pytest.mark.parametrize(
    "updates",
    [
        {"type": "broadcast"},
        {"type": "shutdown_request"},
        {"type": "shutdown_response"},
        {"recipient_kind": "human"},
        {"recipient": "unknown-agent", "to": "unknown-agent"},
        {"to": "different-agent"},
        {"recipient": None},
    ],
)
@pytest.mark.asyncio
async def test_claude_send_message_ignores_non_child_continuations(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, updates: dict[str, Any]
) -> None:
    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        parent_conversation_id=parent.id,
        labels={
            "omnigent.wrapper": "claude-code-native-ui-subagent",
            "omnigent.claude_native.subagent_id": "agent-1",
            CLAUDE_SUBAGENT_OUTCOME_LABEL: "completed",
        },
    )
    publish_status = Mock()
    monkeypatch.setattr("omnigent.server.routes._sessions.helpers._publish_status", publish_status)
    await _persist_external_conversation_items(
        parent.id,
        [
            SessionEventInput(
                type="external_conversation_item",
                data={
                    "source_id": "message",
                    "response_id": "parent-turn",
                    "item_type": "function_call",
                    "item_data": {
                        "agent": "Claude",
                        "name": "SendMessage",
                        "call_id": "tool-2",
                        "arguments": json.dumps(
                            {
                                "type": "message",
                                "recipient_kind": "agent",
                                "recipient": "agent-1",
                                "to": "agent-1",
                                "content": "Continue",
                                **updates,
                            }
                        ),
                    },
                },
            )
        ],
        store,
    )
    publish_status.assert_not_called()
    assert store.get_conversation(child.id).labels == child.labels


@pytest.mark.asyncio
async def test_only_confirmed_results_latch_claude_outcome(
    db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnigent.server.routes._sessions import common, helpers

    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        labels={"omnigent.wrapper": "claude-code-native-ui-subagent"},
    )
    publish_parent = Mock()
    monkeypatch.setattr(helpers, "_publish_child_status_to_parent", publish_parent)
    monkeypatch.setitem(common._session_status_cache, child.id, "failed")
    await record_subagent_activity(child.id, "returned", store, status="failed")
    assert CLAUDE_SUBAGENT_OUTCOME_LABEL not in store.get_conversation(child.id).labels
    helpers._publish_status(child.id, "running")
    assert common._session_status_cache[child.id] == "running"
    assert helpers._child_session_summary_from_conversation(child, parent.id, None).busy

    common._session_status_cache[child.id] = "idle"
    publish_parent.reset_mock()
    await record_subagent_activity(
        child.id, "returned", store, status="completed", turn_id="native-call", confirmed=True
    )
    refreshed = store.get_conversation(child.id)
    assert refreshed.labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"
    publish_parent.assert_called_once_with(child.id, "idle")
    summary = helpers._child_session_summary_from_conversation(refreshed, parent.id, None)
    assert not summary.busy
    assert summary.current_task_status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["sticky_failed", "cold"])
async def test_confirmed_completion_clears_offline_sweep_failure(
    db_uri: str, monkeypatch: pytest.MonkeyPatch, cache_state: str
) -> None:
    """A confirmed non-failed outcome supersedes a speculative offline-sweep failure.

    An offline sweep can mark a child ``failed`` — durable error labels, plus a
    sticky ``failed`` cache entry while the sweeping process stays live. The
    confirmed ``completed`` result must clear that stale failure whether or not
    the cache still holds it (``cold`` models a restart or another replica), so
    the summary stops reading failed and the idle edge reaches the parent's rail.
    """
    from omnigent.server.routes._sessions import common, helpers
    from omnigent.server.schemas import ErrorDetail

    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        labels={"omnigent.wrapper": "claude-code-native-ui-subagent"},
    )
    await helpers._persist_session_status_error_labels(
        child.id,
        ErrorDetail(code="runner_disconnected", message="runner vanished mid-turn"),
        store,
    )
    if cache_state == "sticky_failed":
        common._session_status_cache[child.id] = "failed"
    else:
        common._session_status_cache.pop(child.id, None)
    try:
        failed = helpers._child_session_summary_from_conversation(
            store.get_conversation(child.id), parent.id, None
        )
        assert failed.current_task_status == "failed"
        assert failed.last_task_error is not None

        publish_parent = Mock()
        monkeypatch.setattr(helpers, "_publish_child_status_to_parent", publish_parent)
        await record_subagent_activity(
            child.id, "returned", store, status="completed", turn_id="native-call", confirmed=True
        )

        refreshed = store.get_conversation(child.id)
        assert refreshed.labels[CLAUDE_SUBAGENT_OUTCOME_LABEL] == "completed"
        final = helpers._child_session_summary_from_conversation(refreshed, parent.id, None)
        assert final.current_task_status == "completed"
        assert final.last_task_error is None
        assert not final.busy
        assert common._session_status_cache[child.id] == "idle"
        publish_parent.assert_called_once_with(child.id, "idle")
    finally:
        common._session_status_cache.pop(child.id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
async def test_child_summary_prefers_confirmed_outcome_over_stale_error(
    db_uri: str, outcome: str
) -> None:
    """A confirmed non-failed outcome hides a stale offline-sweep error label.

    A late offline sweep can re-persist ``last_task_error`` after the confirmed
    outcome already latched. The child summary must still project that outcome
    rather than reporting the superseded failure.
    """
    from omnigent.server.routes._sessions import helpers
    from omnigent.server.schemas import ErrorDetail

    store = SqlAlchemyConversationStore(db_uri)
    parent = store.create_conversation()
    child = store.create_conversation(
        kind="sub_agent",
        parent_conversation_id=parent.id,
        labels={
            "omnigent.wrapper": "claude-code-native-ui-subagent",
            CLAUDE_SUBAGENT_OUTCOME_LABEL: outcome,
        },
    )
    await helpers._persist_session_status_error_labels(
        child.id,
        ErrorDetail(code="runner_disconnected", message="late offline sweep"),
        store,
    )
    summary = helpers._child_session_summary_from_conversation(
        store.get_conversation(child.id), parent.id, None
    )
    assert summary.current_task_status == outcome
    assert summary.last_task_error is None
    assert not summary.busy
