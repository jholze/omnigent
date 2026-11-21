"""
In-process tests for standalone-child close and ``sys_session_archive``.

Covers the bug where ``sys_session_close`` rejected (REST) or crashed on
(in-process) children created via ``sys_session_create(agent_id=...)``:
those keep a verbatim title without the ``"<agent>:<title>"`` shape and
have no ``sub_agent_name`` declaration. Also covers the parent-scoped
archive tool's guard rails.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.tools.base import ToolContext
from omnigent.tools.builtins.spawn import (
    _CLOSED_TITLE_INFIX,
    ARCHIVED_BY_PARENT_LABEL_KEY,
    SysSessionArchiveTool,
    SysSessionCloseTool,
)
from omnigent.util.session_lifecycle import (
    CLOSED_LABEL_KEY,
    CLOSED_LABEL_VALUE,
    title_without_closed_marker,
)


@dataclass
class _Tree:
    """
    Hub with one standalone child, one named child and one grandchild.

    :param store: Conversation store under test.
    :param hub: Hub (caller) conversation id.
    :param standalone: ``sys_session_create``-style child (verbatim title).
    :param named: Named ``"<agent>:<title>"`` child.
    :param grandchild: Child of ``named`` (same tree, not a direct child).
    :param ctx: Tool context for the hub.
    """

    store: SqlAlchemyConversationStore
    hub: str
    standalone: str
    named: str
    grandchild: str
    ctx: ToolContext


@pytest.fixture()
def tree(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Tree]:
    """Build the hub tree and point the runtime store accessor at it."""
    store = SqlAlchemyConversationStore(db_uri)
    hub = store.create_conversation(kind="default", title="EQuent hub")
    standalone = store.create_conversation(
        kind="sub_agent",
        title="X | coder | smoke",
        parent_conversation_id=hub.id,
    )
    named = store.create_conversation(
        kind="sub_agent",
        title="researcher:auth",
        parent_conversation_id=hub.id,
        sub_agent_name="researcher",
    )
    grandchild = store.create_conversation(
        kind="sub_agent",
        title="helper:deep",
        parent_conversation_id=named.id,
    )
    monkeypatch.setattr("omnigent.runtime.get_conversation_store", lambda: store)
    ctx = ToolContext(task_id="task_hub", agent_id="ag_hub", conversation_id=hub.id)
    yield _Tree(
        store=store,
        hub=hub.id,
        standalone=standalone.id,
        named=named.id,
        grandchild=grandchild.id,
        ctx=ctx,
    )


def _invoke(tool: object, target: str, ctx: ToolContext) -> dict[str, object]:
    return json.loads(tool.invoke(json.dumps({"conversation_id": target}), ctx))  # type: ignore[attr-defined]


def test_close_standalone_child_with_verbatim_title_succeeds(tree: _Tree) -> None:
    """AC-1 (in-process): close tombstones a verbatim-titled standalone child."""
    out = _invoke(SysSessionCloseTool(), tree.standalone, tree.ctx)
    assert out == {
        "closed": True,
        "conversation_id": tree.standalone,
        "agent": "agent",
        "title": "X | coder | smoke",
    }
    row = tree.store.get_conversation(tree.standalone)
    assert row is not None
    assert row.title == f"X | coder | smoke{_CLOSED_TITLE_INFIX}{tree.standalone}"
    assert title_without_closed_marker(row.title) == "X | coder | smoke"
    assert row.labels[CLOSED_LABEL_KEY] == CLOSED_LABEL_VALUE


def test_close_named_child_keeps_canonical_tombstone(tree: _Tree) -> None:
    """AC-5 regression: named children keep the ``agent:title:closed:id`` form."""
    out = _invoke(SysSessionCloseTool(), tree.named, tree.ctx)
    assert out["agent"] == "researcher"
    assert out["title"] == "auth"
    row = tree.store.get_conversation(tree.named)
    assert row is not None
    assert row.title == f"researcher:auth{_CLOSED_TITLE_INFIX}{tree.named}"


def test_archive_ended_direct_child_archives_closes_and_audits(
    tree: _Tree, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-4: the hub archives its own ended child; never deletes; audited."""
    caplog.set_level(logging.INFO, logger="omnigent.audit.session_archive")
    out = _invoke(SysSessionArchiveTool(), tree.standalone, tree.ctx)
    assert out == {
        "archived": True,
        "conversation_id": tree.standalone,
        "agent": "agent",
        "title": "X | coder | smoke",
    }
    row = tree.store.get_conversation(tree.standalone)
    assert row is not None  # still exists: archive never deletes
    assert row.archived is True
    assert row.labels[CLOSED_LABEL_KEY] == CLOSED_LABEL_VALUE
    assert row.labels[ARCHIVED_BY_PARENT_LABEL_KEY] == tree.hub
    assert any(
        f"caller={tree.hub} target={tree.standalone} outcome=archived" in r.getMessage()
        for r in caplog.records
    )
    # Idempotent second call: no error, flagged as already archived.
    again = _invoke(SysSessionArchiveTool(), tree.standalone, tree.ctx)
    assert again == {
        "archived": True,
        "already_archived": True,
        "conversation_id": tree.standalone,
    }


def test_archive_refuses_grandchild(tree: _Tree) -> None:
    """Same tree but not a DIRECT child: refused, nothing changes."""
    out = _invoke(SysSessionArchiveTool(), tree.grandchild, tree.ctx)
    assert out["error"] == "session_out_of_tree"
    row = tree.store.get_conversation(tree.grandchild)
    assert row is not None and row.archived is False
    assert CLOSED_LABEL_KEY not in row.labels


def test_archive_refuses_top_level_and_other_tree(tree: _Tree) -> None:
    """AC-2/AC-4: top-level (self) and foreign-tree children are refused."""
    assert _invoke(SysSessionArchiveTool(), tree.hub, tree.ctx)["error"] == (
        "session_not_a_sub_agent"
    )
    other_hub = tree.store.create_conversation(kind="default")
    foreign = tree.store.create_conversation(
        kind="sub_agent", title="Y | coder", parent_conversation_id=other_hub.id
    )
    assert _invoke(SysSessionArchiveTool(), foreign.id, tree.ctx)["error"] == (
        "session_out_of_tree"
    )
    row = tree.store.get_conversation(foreign.id)
    assert row is not None and row.archived is False


@pytest.mark.parametrize("status", ["running", "waiting"])
def test_archive_refuses_running_child(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """A child with a live turn is never archived."""
    monkeypatch.setattr(
        "omnigent.tools.builtins.spawn._live_session_status",
        lambda conv_id: status if conv_id == tree.standalone else None,
    )
    out = _invoke(SysSessionArchiveTool(), tree.standalone, tree.ctx)
    assert out["error"] == "session_running"
    row = tree.store.get_conversation(tree.standalone)
    assert row is not None and row.archived is False
    assert CLOSED_LABEL_KEY not in row.labels


def test_archive_schema_is_strict() -> None:
    """Only ``conversation_id``; no delete/force knobs exist."""
    params = SysSessionArchiveTool().get_schema()["function"]["parameters"]
    assert params["required"] == ["conversation_id"]
    assert set(params["properties"]) == {"conversation_id"}
    assert params["additionalProperties"] is False
