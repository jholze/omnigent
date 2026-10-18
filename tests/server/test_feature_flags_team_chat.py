"""Tests for the ``team_chat`` release flag and its ``GET /v1/info`` report."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from omnigent.runtime.agent_cache import AgentCache
from omnigent.server import app as server_app
from omnigent.server.feature_flags import (
    FEATURE_DEFINITIONS,
    FEATURES_ENV_VAR,
    Feature,
    FeatureFlags,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


def _build_app(db_uri: str, tmp_path: Path) -> FastAPI:
    # No explicit ``feature_flags``: the app resolves them from OMNIGENT_FEATURES.
    from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore

    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return server_app.create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / "cache"),
    )


async def _info_features(db_uri: str, tmp_path: Path) -> dict[str, bool]:
    transport = httpx.ASGITransport(app=_build_app(db_uri, tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/info")
    assert resp.status_code == 200
    return resp.json()["features"]


def test_team_chat_defaults_off() -> None:
    assert not FeatureFlags().enabled(Feature.TEAM_CHAT)


def test_team_chat_is_frontend_visible() -> None:
    (definition,) = (d for d in FEATURE_DEFINITIONS if d.feature is Feature.TEAM_CHAT)

    assert definition.frontend_visible


@pytest.mark.asyncio
async def test_ac_002a_info_reports_team_chat_true_when_enabled(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")

    features = await _info_features(db_uri, tmp_path)

    assert features["team_chat"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, ""], ids=["unset", "empty"])
async def test_ac_002b_info_reports_team_chat_false_without_flag(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
) -> None:
    if raw is None:
        monkeypatch.delenv(FEATURES_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(FEATURES_ENV_VAR, raw)

    features = await _info_features(db_uri, tmp_path)

    assert features["team_chat"] is False


@pytest.mark.asyncio
async def test_ac_002b_info_reports_team_chat_false_when_other_flag_enabled(
    db_uri: str,
    runtime_init: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FEATURES_ENV_VAR, "canvas")

    features = await _info_features(db_uri, tmp_path)

    assert features["team_chat"] is False
