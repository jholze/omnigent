"""E2E: a Grok bot polls the team chat over ``/mcp`` with its ogc_ token (T-121).

The test plays the bot's polling routine against the real server: JSON-RPC
``tools/call`` for ``chat_unread``, ``chat_post`` (``confirm: true``) and
``chat_mark_read`` with ``MCP-Protocol-Version: 2025-06-18``, so every result
carries ``structuredContent``. Anna's browser then shows the bot's reply.

On this header-auth server the owner label is the email local part, so the
bot reads "Grok (Jens)" rather than "Grok (Jens Holze)" (Q-29); both pass.

The token lives only in an :class:`IssuedAgentAccount` (repr hides it). No
assertion message includes it or a full message body; markers stand in.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e_ui.team_chat.conftest import (
    ANNA,
    CHAT_TEST_IDS,
    JENS,
    IssuedAgentAccount,
    TeamChatServer,
    fetch_bootstrap,
    issue_agent_account,
    join_channel,
    list_messages,
    revoke_agent_account,
    seed_channel,
    seed_message,
)
from tests.e2e_ui.team_chat.test_agent_thread import (
    _LOAD_TIMEOUT_MS,
    _open_channel,
    _open_thread,
)
from tests.e2e_ui.team_chat.test_viewports import (
    DESKTOP,
    PHONE,
    _assert_no_horizontal_scroll,
)

_VIEWPORTS = pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1280x800", "390x844"])
_PROTOCOL = "2025-06-18"
_BOT_LABEL = re.compile(r"Grok \(Jens( Holze)?\)")
_STREAK_LIMIT = 5


@pytest.fixture
def grok_bot(team_chat_server: TeamChatServer) -> Iterator[IssuedAgentAccount]:
    """Jens's agent account "Grok"; revoked at teardown so its token dies."""
    fetch_bootstrap(team_chat_server, as_user=JENS)
    issued = issue_agent_account(
        team_chat_server,
        as_user=JENS,
        display_name="Grok",
        handle=f"grok-jens-{uuid.uuid4().hex[:6]}",
    )
    yield issued
    revoke_agent_account(team_chat_server, as_user=JENS, account_id=issued.account["id"])


def _bot_headers(bot: IssuedAgentAccount) -> dict[str, str]:
    return {"Authorization": f"Bearer {bot.token}", "MCP-Protocol-Version": _PROTOCOL}


def _bot_channel(server: TeamChatServer, bot: IssuedAgentAccount) -> dict[str, Any]:
    """A channel Jens created; Anna and the bot (through its own token) joined."""
    channel = seed_channel(server, as_user=JENS, name=f"bots-{uuid.uuid4().hex[:6]}")
    fetch_bootstrap(server, as_user=ANNA)
    join_channel(server, as_user=ANNA, channel_id=channel["id"])
    joined = httpx.post(
        f"{server.base_url}/v1/chat/channels/{channel['id']}/join",
        headers={"Authorization": f"Bearer {bot.token}"},
        timeout=10.0,
        trust_env=False,
    )
    assert joined.status_code == 204, f"bot join returned HTTP {joined.status_code}"
    return channel


def _call_tool(
    server: TeamChatServer, bot: IssuedAgentAccount, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One JSON-RPC ``tools/call`` on ``/mcp`` as the bot; return the tool result."""
    resp = httpx.post(
        f"{server.base_url}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=_bot_headers(bot),
        timeout=30.0,
        trust_env=False,
    )
    assert resp.status_code == 200, f"{name}: /mcp returned HTTP {resp.status_code}"
    payload = resp.json()
    assert "error" not in payload, f"{name}: JSON-RPC error {payload.get('error', {}).get('code')}"
    result = payload["result"]
    assert "structuredContent" in result, f"{name}: no structuredContent at {_PROTOCOL}"
    return result


def _ok(result: dict[str, Any], name: str) -> dict[str, Any]:
    structured = result["structuredContent"]
    code = (structured.get("error") or {}).get("code") if isinstance(structured, dict) else None
    assert result.get("isError") is not True, f"{name} failed with code {code}"
    return structured


def _unread_ids(server: TeamChatServer, bot: IssuedAgentAccount) -> list[str]:
    unread = _ok(_call_tool(server, bot, "chat_unread", {}), "chat_unread")
    return [str(item["message_id"]) for item in unread["items"]]


@_VIEWPORTS
def test_bot_polling_unread_post_mark_read_over_mcp_and_reply_shows_grok_jens(
    team_chat_server: TeamChatServer,
    page_as,
    grok_bot: IssuedAgentAccount,
    viewport: dict[str, int],
) -> None:
    """Anna mentions Grok; the bot reads it, replies, marks it read; Anna sees "Grok (Jens)"."""
    channel = _bot_channel(team_chat_server, grok_bot)
    principal = grok_bot.account["principal_id"]
    marker = f"bot-{uuid.uuid4().hex[:8]}"
    mention = seed_message(
        team_chat_server,
        as_user=ANNA,
        channel_id=channel["id"],
        body=f"<@agent_account:{principal}> Status bitte {marker}",
    )

    assert mention["id"] in _unread_ids(team_chat_server, grok_bot), (
        f"chat_unread does not list Anna's mention {marker}"
    )

    posted = _ok(
        _call_tool(
            team_chat_server,
            grok_bot,
            "chat_post",
            {
                "channel": channel["id"],
                "thread_root_id": mention["id"],
                "body": f"Alles grün {marker}",
                "confirm": True,
            },
        ),
        "chat_post",
    )
    reply_id = posted["message"]["message_id"]
    stored = [
        reply
        for reply in list_messages(
            team_chat_server, as_user=ANNA, channel_id=channel["id"], thread_root_id=mention["id"]
        )
        if reply["id"] == reply_id
    ]
    assert [reply.get("author_kind") for reply in stored] == ["agent_account"], (
        f"bot reply {marker} not stored as agent_account"
    )

    marked = _ok(
        _call_tool(
            team_chat_server,
            grok_bot,
            "chat_mark_read",
            {"channel": channel["id"], "message_id": mention["id"], "confirm": True},
        ),
        "chat_mark_read",
    )
    assert marked["channel_id"] == channel["id"]
    assert mention["id"] not in _unread_ids(team_chat_server, grok_bot), (
        f"mention {marker} is still unread after chat_mark_read"
    )

    page = page_as(ANNA, viewport=viewport)
    _open_channel(page, team_chat_server, channel["id"])
    pane = _open_thread(page, marker)
    row = pane.locator(f'[data-testid="{CHAT_TEST_IDS["message"]}"][data-message-id="{reply_id}"]')
    expect(row).to_contain_text(_BOT_LABEL, timeout=_LOAD_TIMEOUT_MS)
    expect(row.get_by_text("Bot", exact=True)).to_be_visible()
    _assert_no_horizontal_scroll(page)


def test_bot_polling_sixth_bot_post_in_streak_thread_returns_bot_streak_limit(
    team_chat_server: TeamChatServer, grok_bot: IssuedAgentAccount
) -> None:
    """After five bot posts in a thread, a sixth chat_post fails with ``bot_streak_limit``."""
    channel = _bot_channel(team_chat_server, grok_bot)
    root = seed_message(
        team_chat_server, as_user=JENS, channel_id=channel["id"], body="Bremse testen"
    )

    def _post(index: int) -> dict[str, Any]:
        return _call_tool(
            team_chat_server,
            grok_bot,
            "chat_post",
            {
                "channel": channel["id"],
                "thread_root_id": root["id"],
                "body": f"bot {index}",
                "confirm": True,
            },
        )

    for index in range(_STREAK_LIMIT):
        _ok(_post(index), f"chat_post #{index + 1}")
    braked = _post(_STREAK_LIMIT)
    assert braked.get("isError") is True, "the sixth bot post was accepted"
    assert braked["structuredContent"]["error"]["code"] == "bot_streak_limit"
    assert braked["content"][0]["text"].startswith("bot_streak_limit:")
