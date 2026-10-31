"""Team-chat latency and capacity measurements that run in normal CI.

Small, in-process versions of the T-130 budgets: change-feed reset (AC-083a),
post-to-frame latency (NFR-001), channel-open tail read (NFR-003) and a
scaled-down multi-client smoke (NFR-026). No locust swarm and no large seed.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import psutil
import pytest
from fastapi.testclient import TestClient

from omnigent.runtime import init as init_runtime
from omnigent.runtime import user_session_stream
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.chat_rate_limit import reset_chat_rate_limits
from omnigent.server.feature_flags import FEATURES_ENV_VAR
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.chat_store.sqlalchemy_store import SqlAlchemyChatStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from tests.server.chat_testkit import ANNA, BOB, CARLA, JENS, build_chat_app, headers_for
from tests.server.test_chat_realtime import _recv_raw, _wait_for_subscriber

_EIGHT_DAYS_US = 8 * 24 * 60 * 60 * 1_000_000
_ZERO_ID = "0" * 32
_SENTINEL = {"type": "hosts_changed"}


def _percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile of *samples* (``fraction`` in 0..1)."""
    ordered = sorted(samples)
    rank = max(1, round(fraction * len(ordered) + 0.5))
    return ordered[min(rank, len(ordered)) - 1]


@pytest.fixture()
def chat_app(db_uri: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Header-auth app with ``team_chat`` on and fresh post windows."""
    monkeypatch.setenv(FEATURES_ENV_VAR, "team_chat")
    artifact_store = LocalArtifactStore(str(tmp_path / "runtime-artifacts"))
    init_runtime(
        conversation_store=SqlAlchemyConversationStore(db_uri),
        agent_store=SqlAlchemyAgentStore(db_uri),
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / ".cache"),
        file_store=SqlAlchemyFileStore(db_uri),
        artifact_store=artifact_store,
    )
    reset_chat_rate_limits()
    with TestClient(build_chat_app(db_uri, tmp_path)) as client:
        yield client
    reset_chat_rate_limits()


def _bootstrap(client: TestClient, user: str) -> dict[str, Any]:
    resp = client.get("/v1/chat/bootstrap", headers=headers_for(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_channel(client: TestClient, owner: str, name: str, members: list[str]) -> str:
    resp = client.post("/v1/chat/channels", json={"name": name}, headers=headers_for(owner))
    assert resp.status_code == 201, resp.text
    channel = resp.json()["id"]
    for member in members:
        joined = client.post(f"/v1/chat/channels/{channel}/join", headers=headers_for(member))
        assert joined.status_code == 204, joined.text
    return channel


def _post(client: TestClient, user: str, channel: str, body: str) -> dict[str, Any]:
    resp = client.post(
        f"/v1/chat/channels/{channel}/messages", json={"body": body}, headers=headers_for(user)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _recv_until_message(ws: Any, message_id: str, *, max_frames: int = 50) -> str:
    """Return the raw ``message.created`` frame for *message_id*."""
    for _ in range(max_frames):
        raw = _recv_raw(ws, {"chat"})
        frame = json.loads(raw)
        if frame.get("kind") == "message.created" and frame.get("message_id") == message_id:
            return raw
    raise AssertionError(f"no frame for {message_id} after {max_frames} chat frames")


def test_ac_083a_http_changes_cursor_older_than_8_days_returns_reset_true(
    chat_app: TestClient,
) -> None:
    _bootstrap(chat_app, ANNA)
    payload = _bootstrap(chat_app, JENS)
    (general,) = (row for row in payload["channels"] if row["name"] == "general")
    message = _post(chat_app, ANNA, general["id"], "recent message")
    stale_after = f"{message['created_at'] - _EIGHT_DAYS_US}:{_ZERO_ID}"

    resp = chat_app.get(
        "/v1/chat/changes", params={"after": stale_after}, headers=headers_for(JENS)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "next": None, "reset": True}


def test_nfr_001_post_commit_to_member_frame_p50_p95(
    chat_app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    channel = _create_channel(chat_app, ANNA, "latency", [JENS])
    markers: list[str] = []
    raws: list[str] = []
    latencies_ms: list[float] = []

    with chat_app.websocket_connect("/v1/sessions/updates", headers=headers_for(JENS)) as ws:
        _wait_for_subscriber(JENS)
        for _ in range(20):
            marker = f"nfr001-marker-{uuid.uuid4().hex}"
            markers.append(marker)
            # Timed from the request start: covers commit, 201 and delivery.
            started = time.perf_counter()
            message = _post(chat_app, ANNA, channel, marker)
            raws.append(_recv_until_message(ws, message["id"]))
            latencies_ms.append((time.perf_counter() - started) * 1000)

    p50 = statistics.median(latencies_ms)
    p95 = _percentile(latencies_ms, 0.95)
    assert p50 <= 250, f"NFR-001 p50 {p50:.1f} ms > 250 ms"
    assert p95 <= 500, f"NFR-001 p95 {p95:.1f} ms > 500 ms"
    log_text = "\n".join(
        f"{record.getMessage()} {record.__dict__}"
        for record in caplog.records
        if record.levelno >= logging.INFO
    )
    for marker in markers:
        assert all(marker not in raw for raw in raws)
        assert marker not in log_text


def test_nfr_003_channel_open_tail_50_p95(chat_app: TestClient, db_uri: str) -> None:
    channel = _create_channel(chat_app, ANNA, "history", [JENS])
    # Seed through the store so the per-channel post window is not involved.
    store = SqlAlchemyChatStore(db_uri)
    for index in range(60):
        store.post_message(channel, ANNA, f"history {index}")
    samples_ms: list[float] = []

    for _ in range(20):
        started = time.perf_counter()
        resp = chat_app.get(
            f"/v1/chat/channels/{channel}/messages",
            params={"tail": "true", "limit": 50},
            headers=headers_for(JENS),
        )
        samples_ms.append((time.perf_counter() - started) * 1000)
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 50

    p95 = _percentile(samples_ms, 0.95)
    assert p95 <= 150, f"NFR-003 tail=50 p95 {p95:.1f} ms > 150 ms"


def test_nfr_026_capacity_smoke_multi_client_no_error(chat_app: TestClient) -> None:
    users = [ANNA, JENS, BOB, CARLA] + [f"load{index}@bloopark.de" for index in range(4)]
    for user in users:
        _bootstrap(chat_app, user)
    channels = [
        _create_channel(chat_app, ANNA, f"capacity-{index}", users[1:]) for index in range(3)
    ]
    process = psutil.Process()
    rss_before = process.memory_info().rss

    with ExitStack() as stack:
        sockets = {
            user: stack.enter_context(
                chat_app.websocket_connect("/v1/sessions/updates", headers=headers_for(user))
            )
            for user in users
        }
        for user in users:
            _wait_for_subscriber(user)
        jobs = [(user, channel) for user in users for channel in channels]
        with ThreadPoolExecutor(max_workers=len(users)) as pool:
            responses = list(
                pool.map(
                    lambda job: chat_app.post(
                        f"/v1/chat/channels/{job[1]}/messages",
                        json={"body": f"capacity {uuid.uuid4().hex}"},
                        headers=headers_for(job[0]),
                    ),
                    jobs,
                )
            )
        assert [resp.status_code for resp in responses] == [201] * len(jobs)
        posted_ids = {resp.json()["id"] for resp in responses}
        received: dict[str, set[str]] = {}
        for user, ws in sockets.items():
            # Frames reach one subscriber in publish order, so the sentinel ends the drain.
            user_session_stream.publish(user, dict(_SENTINEL))
            seen: set[str] = set()
            for _ in range(len(jobs) * 4):
                frame = json.loads(_recv_raw(ws, {"chat", "hosts_changed"}))
                if frame["type"] == "hosts_changed":
                    break
                assert "body" not in frame
                if frame["kind"] == "message.created":
                    seen.add(frame["message_id"])
            received[user] = seen

    rss_growth_mb = (process.memory_info().rss - rss_before) / (1024 * 1024)
    for user in users:
        assert received[user] == posted_ids, user
    assert rss_growth_mb <= 200, f"NFR-026 RSS grew {rss_growth_mb:.1f} MB"
