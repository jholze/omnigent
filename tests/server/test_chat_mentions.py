"""Tests for the pure team-chat mention-token parser."""

from __future__ import annotations

from omnigent.server.chat_mentions import MentionToken, parse_mentions


def test_parses_all_three_token_kinds() -> None:
    body = (
        "hey <@user:anna@bloopark.de> and <@agent_account:agent:111> and <@omnigent_agent:coder>"
    )
    assert parse_mentions(body) == [
        MentionToken(kind="user", target_id="anna@bloopark.de"),
        MentionToken(kind="agent_account", target_id="agent:111"),
        MentionToken(kind="omnigent_agent", target_id="coder"),
    ]


def test_unknown_kind_is_ignored() -> None:
    assert parse_mentions("<@channel:general> and <@user:anna@bloopark.de>") == [
        MentionToken(kind="user", target_id="anna@bloopark.de")
    ]


def test_empty_target_is_ignored() -> None:
    assert parse_mentions("<@user:> and <@user:anna@bloopark.de>") == [
        MentionToken(kind="user", target_id="anna@bloopark.de")
    ]


def test_unclosed_token_is_ignored() -> None:
    assert parse_mentions("<@user:anna@bloopark.de") == []


def test_repeated_targets_are_all_returned() -> None:
    body = "<@user:anna@bloopark.de> und <@user:anna@bloopark.de>"
    assert parse_mentions(body) == [
        MentionToken(kind="user", target_id="anna@bloopark.de"),
        MentionToken(kind="user", target_id="anna@bloopark.de"),
    ]


def test_no_mentions_returns_empty() -> None:
    assert parse_mentions("plain text ohne token") == []
