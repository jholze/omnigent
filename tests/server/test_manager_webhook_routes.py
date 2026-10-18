"""GET /v1/sessions/{id}/manager-webhook-deliveries."""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI

from tests.server.helpers import create_test_agent


async def test_lists_delivery_status_without_the_payload(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    agent = await create_test_agent(client, name="webhook-status")
    created = await client.post("/v1/sessions", json={"agent_id": agent["id"]})
    assert created.status_code == 201
    session_id = created.json()["id"]
    store = app.state.session_lifecycle_store
    assert store is not None
    event_id = uuid.uuid4().hex
    store.record_lifecycle_event(
        event_id=event_id,
        session_id=session_id,
        event_type="session.awaiting_decision",
        transition_key="elicitation:e1:awaiting_decision",
        payload='{"message":"secret-body"}',
        now=50,
    )
    response = await client.get(f"/v1/sessions/{session_id}/manager-webhook-deliveries")
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    delivery = body["deliveries"][0]
    assert delivery["event_id"] == event_id
    assert delivery["event_type"] == "session.awaiting_decision"
    assert delivery["status"] == "pending"
    assert "payload" not in delivery
    assert "secret-body" not in response.text

    missing = await client.get(f"/v1/sessions/{uuid.uuid4().hex}/manager-webhook-deliveries")
    assert missing.status_code == 404


async def test_delivery_status_requires_manage_and_hides_errors_from_readers(
    auth_client: httpx.AsyncClient, auth_app: FastAPI
) -> None:
    from omnigent.server.auth import LEVEL_MANAGE, LEVEL_READ, RESERVED_USER_PUBLIC
    from tests.server.helpers import create_test_agent

    owner = "owner@example.com"
    agent = await create_test_agent(auth_client, name="webhook-auth", user=owner)
    session_id = agent["_session_id"]
    store = auth_app.state.session_lifecycle_store
    assert store is not None
    event_id = uuid.uuid4().hex
    store.record_lifecycle_event(
        event_id=event_id,
        session_id=session_id,
        event_type="session.failed",
        transition_key="failure:auth-route",
        payload="{}",
        now=50,
    )
    claimed = store.claim_batch(limit=1, now=50, lease_owner="host:1", lease_seconds=30)
    store.mark_delivery_failed(
        claimed[0].id,
        workspace_id=claimed[0].workspace_id,
        next_attempt_at=99,
        dead_letter_after_attempts=1,
        http_status=500,
        error_code="http_error",
        error_message="receiver exploded",
    )
    path = f"/v1/sessions/{session_id}/manager-webhook-deliveries"
    owner_headers = {"X-Forwarded-Email": owner}
    owner_resp = await auth_client.get(path, headers=owner_headers)
    assert owner_resp.status_code == 200
    assert owner_resp.json()["deliveries"][0]["last_error_message"] == "receiver exploded"

    stranger = await auth_client.get(path, headers={"X-Forwarded-Email": "stranger@example.com"})
    assert stranger.status_code == 404

    public_grant = await auth_client.put(
        f"/v1/sessions/{session_id}/permissions",
        json={"user_id": RESERVED_USER_PUBLIC, "level": LEVEL_READ},
        headers=owner_headers,
    )
    assert public_grant.status_code == 200, public_grant.text
    viewer = await auth_client.get(path, headers={"X-Forwarded-Email": "viewer@example.com"})
    assert viewer.status_code == 403
    assert "receiver exploded" not in viewer.text

    manage_grant = await auth_client.put(
        f"/v1/sessions/{session_id}/permissions",
        json={"user_id": "manager@example.com", "level": LEVEL_MANAGE},
        headers=owner_headers,
    )
    assert manage_grant.status_code == 200, manage_grant.text
    managed = await auth_client.get(path, headers={"X-Forwarded-Email": "manager@example.com"})
    assert managed.status_code == 200
    assert managed.json()["deliveries"][0]["last_error_message"] == "receiver exploded"
