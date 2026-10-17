"""Fan-out of team-chat frames to each member's updates stream.

Frames flow through the existing ``WS /v1/sessions/updates`` push: the
route publishes a ``chat`` event to each eligible principal's
:mod:`omnigent.runtime.user_session_stream` channel, and the websocket
loop forwards it to the browser. A frame never carries a message body
(NFR-014) — the client fetches content over REST after receiving the
frame.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from omnigent.runtime import user_session_stream
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.routes._sessions.helpers import _discovery_key
from omnigent.stores.chat_store import ChatChannel, ChatMessage, ChatStore


def publish_chat(principals: Iterable[str], frame: Mapping[str, object]) -> None:
    """Publish one chat frame to each principal's updates stream.

    :param principals: Principal ids the frame is delivered to. Members
        with no open stream simply drop the event.
    :param frame: Frame fields without ``type``; ``"chat"`` is added here.
    """
    payload = dict(frame)
    payload["type"] = "chat"
    for principal in principals:
        # The no-auth caller is stored as ``RESERVED_USER_LOCAL`` ("local"),
        # but its updates socket subscribes under the shared single-user
        # discovery key ``_discovery_key(None)``. Routing the frame to
        # ``_discovery_key("local")`` would send it to a channel nobody
        # listens on, so map the reserved principal back to the shared key.
        key = (
            _discovery_key(None) if principal == RESERVED_USER_LOCAL else _discovery_key(principal)
        )
        user_session_stream.publish(key, payload)


def publish_message_created(store: ChatStore, channel: ChatChannel, message: ChatMessage) -> None:
    """Announce a stored message to every member of its channel.

    The frame carries ``channel_id``, ``message_id``, ``thread_root_id``,
    ``created_at`` and ``author_id`` and no body (FR-014).
    """
    members = [member.principal_id for member in store.list_members(channel.id)]
    publish_chat(
        members,
        {
            "kind": "message.created",
            "channel_id": channel.id,
            "message_id": message.id,
            "thread_root_id": message.thread_root_id,
            "created_at": message.created_at,
            "author_id": message.author_id,
        },
    )
