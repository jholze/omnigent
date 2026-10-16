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
