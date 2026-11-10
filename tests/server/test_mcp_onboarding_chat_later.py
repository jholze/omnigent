"""Chat-shaped onboarding cases that this server does not implement.

Findings and owner hints are returned by ``onboarding_status``. Nothing is
posted with ``chat_post`` or as a direct message. These tests stay skipped
until ``OMNIGENT_FEATURES`` includes ``team_chat``. With that flag they fail
on purpose, so an empty body cannot pass.
"""

from __future__ import annotations

import os

import pytest

_CHAT_IN_PRODUCT = "team_chat" in {
    part.strip() for part in os.environ.get("OMNIGENT_FEATURES", "").split(",") if part.strip()
}

pytestmark = pytest.mark.skipif(
    not _CHAT_IN_PRODUCT,
    reason="later, when chat is in the product",
)


def test_chat_02_direct_message_is_not_used() -> None:
    """CHAT-02: a review finding is not delivered as a direct message."""
    pytest.fail("later, when chat is in the product")


def test_chat_03_agent_secret_is_not_a_chat_credential() -> None:
    """CHAT-03: the agent token is not used to open a chat channel."""
    pytest.fail("later, when chat is in the product")


def test_tc01_review_findings_are_not_a_direct_message() -> None:
    """TC-01 chat portion: review findings stay on onboarding_status."""
    pytest.fail("later, when chat is in the product")


def test_tc05_owner_hint_is_not_a_chat_post() -> None:
    """TC-05 chat portion: the owner hint is an owner notice, not a chat post."""
    pytest.fail("later, when chat is in the product")


def test_tc19_rework_findings_are_not_a_direct_message() -> None:
    """TC-19 chat portion: each REWORK round stays on onboarding_status."""
    pytest.fail("later, when chat is in the product")
