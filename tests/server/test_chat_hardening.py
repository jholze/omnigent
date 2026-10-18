"""Message bodies stay out of realtime frames (NFR-014)."""

from __future__ import annotations

from tests.server.chat_testkit import ANNA, JENS, channel_id, chat_client, join, post_message

_ = chat_client
_MARKER = "MARKER-7f3a secret body"


async def test_message_created_frame_omits_the_body(chat_client, monkeypatch) -> None:
    frames: list[dict] = []

    def spy(principals, frame):
        frames.append(dict(frame))

    monkeypatch.setattr("omnigent.server.chat_realtime.publish_chat", spy)
    billing = await channel_id(chat_client, ANNA, "billing")
    await join(chat_client, JENS, billing)
    posted = await post_message(chat_client, ANNA, billing, _MARKER)
    assert posted.status_code == 201, posted.text
    assert frames
    blob = " ".join(str(value) for frame in frames for value in frame.values())
    assert _MARKER not in blob
    assert "body" not in frames[0]
