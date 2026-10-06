"""E2E coverage for recovering a missed session-status event."""

from __future__ import annotations

import json

import httpx
from playwright.sync_api import Page, expect

_WORKING = '[data-testid="working-indicator"]'
_HEARTBEAT_INTERVAL_MS = 10_000


def _publish_status(base_url: str, session_id: str, status: str) -> None:
    """Publish a durable status edge through the native-harness event route."""
    response = httpx.post(
        f"{base_url}/v1/sessions/{session_id}/events",
        json={"type": "external_session_status", "data": {"status": status}},
        timeout=10.0,
    )
    response.raise_for_status()


def _snapshot_status(base_url: str, session_id: str) -> str:
    """Read the status exposed by the slim session snapshot."""
    response = httpx.get(f"{base_url}/v1/sessions/{session_id}", timeout=10.0)
    response.raise_for_status()
    return str(response.json()["status"])


def _install_heartbeat_only_stream(page: Page, session_id: str) -> None:
    """Replace this session's stream with a live heartbeat-only transport."""
    script = """
        (() => {
          const sessionId = __SESSION_ID__;
          const heartbeatInterval = __HEARTBEAT_INTERVAL__;
          const originalFetch = window.fetch.bind(window);
          window.__statusGapStreamOpens = 0;
          window.__statusGapHeartbeats = 0;
          window.fetch = (input, init) => {
            const url = typeof input === "string" ? input : input.url;
            const streamPath = `/v1/sessions/${sessionId}/stream`;
            if (new URL(url, window.location.origin).pathname !== streamPath) {
              return originalFetch(input, init);
            }

            window.__statusGapStreamOpens += 1;
            let heartbeatTimer;
            const body = new ReadableStream({
              start(controller) {
                const sendHeartbeat = () => {
                  const frame = "event: session.heartbeat\\ndata: {}\\n\\n";
                  controller.enqueue(new TextEncoder().encode(frame));
                  window.__statusGapHeartbeats += 1;
                };
                sendHeartbeat();
                heartbeatTimer = window.setInterval(sendHeartbeat, heartbeatInterval);
              },
              cancel() {
                window.clearInterval(heartbeatTimer);
              },
            });
            return Promise.resolve(new Response(body, {
              status: 200,
              headers: { "content-type": "text/event-stream" },
            }));
          };
        })()
        """
    page.add_init_script(
        script.replace("__SESSION_ID__", json.dumps(session_id)).replace(
            "__HEARTBEAT_INTERVAL__", str(_HEARTBEAT_INTERVAL_MS)
        )
    )


def test_snapshot_idle_clears_working_on_heartbeat_only_stream(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """A visible chat recovers when its stream misses the terminal status edge."""
    base_url, session_id = seeded_session
    _publish_status(base_url, session_id, "running")
    assert _snapshot_status(base_url, session_id) == "running"

    page.clock.install()
    _install_heartbeat_only_stream(page, session_id)
    page.goto(f"{base_url}/c/{session_id}")

    working = page.locator(_WORKING)
    expect(working).to_be_visible(timeout=15_000)
    page.wait_for_function("window.__statusGapHeartbeats > 0")

    # The server reaches idle, but this tab's healthy stream never receives
    # that lifecycle event. Only snapshot reconciliation can clear the UI.
    _publish_status(base_url, session_id, "idle")
    assert _snapshot_status(base_url, session_id) == "idle"
    expect(working).to_be_visible()

    # Heartbeats keep the transport healthy, and the missing idle edge leaves
    # the stale indicator untouched before the reconciliation interval.
    page.clock.run_for("00:50")
    expect(working).to_be_visible()
    assert page.evaluate("window.__statusGapStreamOpens") == 1

    page.clock.run_for("00:10")

    expect(working).to_have_count(0, timeout=15_000)
    assert page.evaluate("window.__statusGapStreamOpens") == 1
    assert page.evaluate("window.__statusGapHeartbeats") >= 6


def _rewrite_snapshot_idle(route) -> None:
    """Make the slim session snapshot report ``idle`` while leaving the body."""
    # Guard the teardown race: when the browser closes while a polled snapshot
    # is still in flight, fetching or fulfilling the now-disposed response
    # raises. That must not fail the already-finished test, so swallow it — the
    # dropped request dies with the context.
    try:
        resp = route.fetch()
        try:
            body = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        if isinstance(body, dict) and body.get("status"):
            body["status"] = "idle"
            body["active_response_id"] = None
            body["background_task_count"] = 0
            body["background_tasks"] = []
        route.fulfill(response=resp, json=body)
    except Exception:
        return


def test_running_turn_relights_working_after_reconnect_from_stale_snapshot(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """A chat that binds mid-turn must still show the working indicator.

    The SSE stream is snapshot + live tail with no replay of the turn-start
    ``running`` edge, so a client (re)connecting mid-turn depends on the
    ``getSession`` snapshot carrying ``running``. The persisted row lags the
    live push, so a bind landing in that window reads ``idle`` and never shows
    the indicator for the rest of the turn. The stale snapshot is injected as a
    deterministic stand-in for the production row-lag/reconnect race.
    """
    base_url, session_id = seeded_session
    _publish_status(base_url, session_id, "running")
    assert _snapshot_status(base_url, session_id) == "running"

    page.goto(f"{base_url}/c/{session_id}")
    expect(page.locator(_WORKING)).to_be_visible(timeout=15_000)

    page.route(f"**/v1/sessions/{session_id}", _rewrite_snapshot_idle)
    page.route(f"**/v1/sessions/{session_id}?*", _rewrite_snapshot_idle)
    assert _snapshot_status(base_url, session_id) == "running"

    page.reload()
    expect(page.locator(_WORKING)).to_be_visible(timeout=20_000)
    # Stop intercepting before teardown so a background poll in flight when the
    # browser closes can't fail the finished test on a disposed response.
    page.unroute(f"**/v1/sessions/{session_id}")
    page.unroute(f"**/v1/sessions/{session_id}?*")
