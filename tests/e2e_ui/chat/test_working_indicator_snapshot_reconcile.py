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


def _install_stale_idle_snapshot(page: Page, session_id: str) -> None:
    """Serve this session's snapshot GET as ``idle`` once armed via sessionStorage."""
    script = """(() => {
      const sessionId = __SESSION_ID__;
      const snapshotPath = `/v1/sessions/${sessionId}`;
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = typeof input === "string" ? input : input.url;
        const method =
          (init && init.method) ||
          (typeof input === "object" && input && input.method) ||
          "GET";
        if (
          window.sessionStorage.getItem("__forceIdleSnapshot") !== "1" ||
          method.toUpperCase() !== "GET" ||
          new URL(url, window.location.origin).pathname !== snapshotPath
        ) {
          return originalFetch(input, init);
        }
        const response = await originalFetch(input, init);
        let body;
        try {
          body = await response.clone().json();
        } catch (err) {
          return response;
        }
        if (body && typeof body === "object" && body.status) {
          body.status = "idle";
          body.active_response_id = null;
          body.background_task_count = 0;
          body.background_tasks = [];
        }
        const headers = new Headers(response.headers);
        headers.set("content-type", "application/json");
        return new Response(JSON.stringify(body), {
          status: response.status,
          statusText: response.statusText,
          headers,
        });
      };
    })()"""
    # An in-page override cannot outlive the browser the way a Playwright route handler
    # can (and fail teardown on a disposed response); the flag keeps the first bind on
    # the real snapshot so only the reconnect sees the lagging ``idle``.
    page.add_init_script(script.replace("__SESSION_ID__", json.dumps(session_id)))


def test_running_turn_relights_working_after_reconnect_from_stale_snapshot(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """A chat that binds mid-turn must still show the working indicator.

    The injected stale ``idle`` snapshot stands in for the production row-lag race."""
    base_url, session_id = seeded_session
    _publish_status(base_url, session_id, "running")
    assert _snapshot_status(base_url, session_id) == "running"

    _install_stale_idle_snapshot(page, session_id)
    page.goto(f"{base_url}/c/{session_id}")
    expect(page.locator(_WORKING)).to_be_visible(timeout=15_000)

    # Arm the client-side rewrite so only the reconnect's snapshot reads a
    # lagging ``idle`` while the server still reports the turn as ``running``.
    page.evaluate("window.sessionStorage.setItem('__forceIdleSnapshot', '1')")
    assert _snapshot_status(base_url, session_id) == "running"

    page.reload()
    expect(page.locator(_WORKING)).to_be_visible(timeout=20_000)
