"""Best-effort session context for manager-webhook payloads.

Lookups fail open: a missing title or project name is omitted from the
payload, and a store error never blocks the outbox write.
"""

from __future__ import annotations

from collections.abc import Callable

from omnigent.server.session_outbox import SessionPushContext
from omnigent.stores.agent_store import AgentStore
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.project_store import ProjectStore


def build_lookups(
    *,
    conversation_store: ConversationStore,
    agent_store: AgentStore | None = None,
    project_store: ProjectStore | None = None,
) -> tuple[Callable[[str], SessionPushContext | None], Callable[[str], str | None]]:
    """Return ``(context_lookup, summary_lookup)`` closed over the stores."""

    def context_lookup(session_id: str) -> SessionPushContext | None:
        conversation = conversation_store.get_conversation(session_id)
        if conversation is None:
            return None
        agent_name = conversation.sub_agent_name
        if agent_store is not None and conversation.agent_id:
            agent = agent_store.get(conversation.agent_id)
            if agent is not None and agent.name:
                agent_name = agent.name
        project_name = None
        if project_store is not None and conversation.project_id:
            project = project_store.get(conversation.project_id, user_id=None)
            if project is not None:
                project_name = project.name
        return SessionPushContext(
            title=conversation.title,
            project_id=conversation.project_id,
            project_name=project_name,
            agent_name=agent_name,
        )

    def summary_lookup(session_id: str) -> str | None:
        page = conversation_store.list_items(session_id, limit=8, order="desc")
        for item in page.data:
            if getattr(item, "type", None) != "message":
                continue
            data = item.data
            role = getattr(data, "role", None)
            if role not in (None, "assistant"):
                continue
            text = _message_text(data)
            if text:
                return text
        return None

    return context_lookup, summary_lookup


def _message_text(data: object) -> str | None:
    content = getattr(data, "content", None)
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    joined = "\n".join(parts).strip()
    return joined or None
