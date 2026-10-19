"""Registry MCP caller identity stays off the local user in accounts mode."""

from __future__ import annotations

import pytest

from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.registry_gateway import registry_user
from omnigent.server.routes.connections_base import ConnectionError


def test_registry_user_prefers_the_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")

    assert registry_user({"run_as": "alice"}) == "alice"


def test_registry_user_falls_back_only_for_local_header_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-user marker is not enough: accounts mode must not use local."""
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")

    assert registry_user(None) == RESERVED_USER_LOCAL

    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")
    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user(None)

    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "oidc")
    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user({})


def test_registry_user_without_the_marker_requires_an_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "0")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")

    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user(None)
