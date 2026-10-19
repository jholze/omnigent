"""Registry MCP caller identity follows the app's auth provider."""

from __future__ import annotations

import pytest

from omnigent.server.auth import RESERVED_USER_LOCAL, UnifiedAuthProvider
from omnigent.server.registry_gateway import registry_user
from omnigent.server.routes.connections_base import ConnectionError


def test_registry_user_prefers_the_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")

    assert registry_user({"run_as": "alice"}, UnifiedAuthProvider(source="accounts")) == "alice"


def test_registry_user_falls_back_only_for_local_header_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-user marker is not enough: accounts mode must not use local."""
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    # The process env says header. The app provider is what /v1/info uses.
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")
    header = UnifiedAuthProvider(source="header")

    assert registry_user(None, header) == RESERVED_USER_LOCAL

    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user(None, UnifiedAuthProvider(source="accounts"))

    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user({}, UnifiedAuthProvider(source="oidc"))


def test_registry_user_without_the_marker_requires_an_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "0")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "header")

    with pytest.raises(ConnectionError, match="authenticated user"):
        registry_user(None, UnifiedAuthProvider(source="header"))


def test_registry_user_without_a_provider_uses_the_local_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No provider is /v1/info mode ``none``, which still counts as local."""
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")

    assert registry_user(None, None) == RESERVED_USER_LOCAL
