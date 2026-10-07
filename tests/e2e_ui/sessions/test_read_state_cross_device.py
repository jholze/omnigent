"""Browser e2e: cross-device session read/unread state syncs to a user's other
open clients. Two Chromium contexts stand in as desktop and mobile (Pixel 7
profile with an Android bridge stub that records ``setBadgeCount``)."""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Response,
    WebSocket,
    expect,
)

from tests.e2e_ui.conftest import (
    _create_runner_bound_session,
    _ensure_runner_online,
    _server_state,
    configure_mock_llm,
)

_REPLY_DELAY_S = 4.0
_TURN_TIMEOUT_MS = 90_000
# The second client's list refreshes over WS /v1/sessions/updates or the
# connected-stream reconcile poll (60 s); allow one full interval plus slack.
_LIST_REFRESH_TIMEOUT_S = 75.0
_UNSEEN_DOT = '[data-testid="session-state-badge"][data-state="unseen"]'
_UNREAD_ROW = '[data-testid="inbox-unread"]'
_SIDEBAR = 'aside[aria-label="Conversations"]'

_ANDROID_SHELL_INIT_SCRIPT = """window.__badgeCalls = [];
window.omnigentNative = { kind: "android" };
window.omnigentNative.setBadgeCount = (count) => window.__badgeCalls.push({ count });"""


@pytest.fixture
def three_sessions(
    live_server: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, list[str]]]:
    """Three runner-bound ``hello_world`` sessions, deleted on teardown."""
    respawned = _ensure_runner_online(live_server, tmp_path_factory)
    runner_id = str(_server_state["runner_id"])
    ids: list[str] = []
    try:
        for _ in range(3):
            ids.append(_create_runner_bound_session(live_server, runner_id))
        yield live_server, ids
    finally:
        for sid in ids:
            # Best-effort cleanup must not mask the test result.
            with contextlib.suppress(httpx.HTTPError):
                httpx.delete(f"{live_server}/v1/sessions/{sid}", timeout=10.0)
        if respawned is not None:
            respawned.terminate()
            try:
                respawned.wait(timeout=5)
            except subprocess.TimeoutExpired:
                respawned.kill()
                respawned.wait(timeout=5)


def _row(page: Page, session_id: str) -> Locator:
    """Locate the sidebar row (``<li>``) for *session_id* by its link."""
    return page.locator(f"{_SIDEBAR} li").filter(has=page.locator(f'a[href="/c/{session_id}"]'))


def _unread_dot(row: Locator) -> Locator:
    return row.locator(_UNSEEN_DOT)


def _open_session(page: Page, session_id: str) -> None:
    """Click the row for *session_id*, retrying until the URL and ``main`` pane both
    carry this id. Rows re-sort as replies land (a click can hit a neighbour) and
    the URL changes before the transcript re-binds, so a naive click races both."""
    link = _row(page, session_id).locator(f'a[href="/c/{session_id}"]')
    for attempt in range(3):
        link.click()
        try:
            expect(page).to_have_url(re.compile(rf"/c/{session_id}$"), timeout=5_000)
            expect(page.locator(f'main[data-session-id="{session_id}"]')).to_be_visible(
                timeout=5_000
            )
            return
        except AssertionError:
            if attempt == 2:
                raise


def _last_badge(page: Page) -> int | None:
    return page.evaluate("() => window.__badgeCalls.at(-1)?.count ?? null")


def _wait_badge(page: Page, count: int, timeout_ms: int) -> None:
    page.wait_for_function(
        "n => window.__badgeCalls.length > 0 && window.__badgeCalls.at(-1).count === n",
        arg=count,
        timeout=timeout_ms,
    )


def _walk_rows(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict carrying a session ``id`` and ``viewer_last_seen``."""
    if isinstance(node, dict):
        if "id" in node and "viewer_last_seen" in node:
            yield node
        for value in node.values():
            yield from _walk_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_rows(value)


@dataclass
class _ListObserver:
    """Read-state a client received from the server: the highest per-session
    ``viewer_last_seen`` and the latest ``viewer_unread`` flag."""

    seen: dict[str, int] = field(default_factory=dict)
    unread: dict[str, bool] = field(default_factory=dict)

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)
        page.on("websocket", self._on_websocket)

    def _ingest(self, payload: Any) -> None:
        for row in _walk_rows(payload):
            value = row.get("viewer_last_seen")
            if isinstance(value, int):
                self.seen[row["id"]] = max(self.seen.get(row["id"], 0), value)
            if "viewer_unread" in row:
                self.unread[row["id"]] = bool(row.get("viewer_unread"))

    def _on_response(self, response: Response) -> None:
        if response.request.method != "GET" or urlparse(response.url).path != "/v1/sessions":
            return
        try:
            payload = response.json()
        except Exception:
            # A body that is gone or not JSON is not a list refresh.
            return
        self._ingest(payload)

    def _on_websocket(self, ws: WebSocket) -> None:
        if not urlparse(ws.url).path.endswith("/v1/sessions/updates"):
            return

        def on_frame(payload: str | bytes) -> None:
            try:
                parsed = json.loads(payload)
            except Exception:
                # Non-JSON frames carry no rows.
                return
            self._ingest(parsed)

        ws.on("framereceived", on_frame)

    def synced(self, session_ids: list[str], floor: int) -> bool:
        return all(self.seen.get(sid, 0) >= floor for sid in session_ids)

    def unread_flagged(self, session_id: str) -> bool:
        return self.unread.get(session_id, False)


def _wait_for_list_refresh(
    page: Page, observer: _ListObserver, session_ids: list[str], floor: int
) -> bool:
    """Wait until *observer* has the server read-state (``>= floor``) for every
    session, so the caller can assert the mobile's own list carried the desktop
    read before checking the UI; returns whether every session reached *floor*."""
    deadline = time.monotonic() + _LIST_REFRESH_TIMEOUT_S
    while time.monotonic() < deadline:
        if observer.synced(session_ids, floor):
            return True
        # Playwright event handlers only run while a Playwright call is pending.
        page.wait_for_timeout(500)
    return observer.synced(session_ids, floor)


def _wait_for_unread_flag(page: Page, observer: _ListObserver, session_id: str) -> bool:
    """Wait until *observer* received ``viewer_unread`` true for *session_id*, so a
    surfaced (or missing) dot can only be the client's handling of the list, not a
    dropped refresh; returns whether the flag arrived within the refresh window."""
    deadline = time.monotonic() + _LIST_REFRESH_TIMEOUT_S
    while time.monotonic() < deadline:
        if observer.unread_flagged(session_id):
            return True
        page.wait_for_timeout(500)
    return observer.unread_flagged(session_id)


def _send_and_leave(desktop: Page, session_id: str, text: str) -> None:
    """Open *session_id*, send *text*, and move to the Inbox before the reply lands."""
    _open_session(desktop, session_id)
    composer = desktop.get_by_label("Message the agent")
    expect(composer).to_be_enabled()
    composer.fill(text)
    composer.press("Enter")
    expect(
        desktop.locator('[data-testid="message-bubble"][data-role="user"]').filter(has_text=text)
    ).to_be_visible()
    desktop.locator(f'{_SIDEBAR} a[href="/inbox"]').first.click()
    expect(desktop).to_have_url(re.compile(r"/inbox$"))


def test_read_on_desktop_clears_unread_on_open_mobile_client(
    playwright: Playwright,
    browser: Browser,
    three_sessions: tuple[str, list[str]],
    mock_llm_server_url: str,
    output_path: str,
) -> None:
    """Reading sessions on desktop clears them on an already-open mobile client:
    desktop makes three unread, opens two and marks the third read, then without a
    reload the mobile shows no Inbox unread rows, no pill, badge 0 and no dots."""
    base_url, session_ids = three_sessions
    markers = {sid: f"xdev-{uuid.uuid4().hex[:8]}" for sid in session_ids}
    for i, sid in enumerate(session_ids, start=1):
        # Several copies per marker so a title-generation call can't drain the queue.
        configure_mock_llm(
            mock_llm_server_url,
            [{"text": f"Task {i} finished: all tests green.", "delay": _REPLY_DELAY_S}] * 3,
            match=markers[sid],
        )
    artifacts = Path(output_path)
    artifacts.mkdir(parents=True, exist_ok=True)

    mobile_ctx = browser.new_context(**playwright.devices["Pixel 7"])
    mobile_ctx.add_init_script(_ANDROID_SHELL_INIT_SCRIPT)
    desktop_ctx: BrowserContext | None = None
    try:
        desktop_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        mobile = mobile_ctx.new_page()
        desktop = desktop_ctx.new_page()
        observer = _ListObserver()
        observer.attach(mobile)

        mobile.goto(f"{base_url}/?sidebar=open")
        desktop.goto(f"{base_url}/inbox")
        for sid in session_ids:
            expect(_row(desktop, sid)).to_be_visible(timeout=30_000)
            expect(_row(mobile, sid)).to_be_visible(timeout=30_000)
            expect(_unread_dot(_row(mobile, sid))).to_have_count(0)
        _wait_badge(mobile, 0, timeout_ms=30_000)

        for i, sid in enumerate(session_ids, start=1):
            _send_and_leave(desktop, sid, f"Finish task {i} and report. Marker: {markers[sid]}")

        for sid in session_ids:
            expect(_unread_dot(_row(desktop, sid))).to_be_visible(timeout=_TURN_TIMEOUT_MS)
            expect(_unread_dot(_row(mobile, sid))).to_be_visible(timeout=_TURN_TIMEOUT_MS)
        expect(desktop.locator(_UNREAD_ROW)).to_have_count(3)
        _wait_badge(mobile, 3, timeout_ms=30_000)
        mobile.locator(f'{_SIDEBAR} a[href="/inbox"]').first.tap()
        expect(mobile).to_have_url(re.compile(r"/inbox$"))
        expect(mobile.locator(_UNREAD_ROW)).to_have_count(3)
        expect(mobile.get_by_title("3 unread")).to_be_visible()
        mobile.screenshot(path=str(artifacts / "mobile-before-desktop-read.png"))

        read_floor = int(time.time())
        for sid in session_ids[:2]:
            _open_session(desktop, sid)
            expect(
                desktop.locator('[data-testid="message-bubble"][data-role="assistant"]').last
            ).to_be_visible(timeout=30_000)
            expect(_unread_dot(_row(desktop, sid))).to_have_count(0)
        third = _row(desktop, session_ids[2])
        third.hover()
        third.get_by_test_id("conversation-actions").click()
        desktop.get_by_test_id("mark-read-conversation").click()
        expect(_unread_dot(third)).to_have_count(0)
        desktop.locator(f'{_SIDEBAR} a[href="/inbox"]').first.click()
        expect(desktop.locator(_UNREAD_ROW)).to_have_count(0)
        desktop.screenshot(path=str(artifacts / "desktop-after-read.png"))

        synced = _wait_for_list_refresh(mobile, observer, session_ids, read_floor)
        print(f"mobile list carried desktop read-state: {synced} ({observer.seen})")
        mobile.screenshot(path=str(artifacts / "mobile-after-desktop-read.png"))
        print(f"mobile badge after desktop read: {_last_badge(mobile)}")

        # The mobile's own list must carry the desktop read, so a cleared badge
        # and rows below can only be the fix, not a dropped refresh.
        assert synced, f"mobile never received the desktop read-state: {observer.seen}"
        expect(mobile.locator(_UNREAD_ROW)).to_have_count(0, timeout=15_000)
        expect(mobile.get_by_title(re.compile(r"\bunread\b"))).to_have_count(0)
        _wait_badge(mobile, 0, timeout_ms=15_000)
        toggle = mobile.get_by_role("button", name="Open sidebar")
        if toggle.count() > 0:
            toggle.tap()
        for sid in session_ids:
            expect(_row(mobile, sid)).to_be_visible()
            expect(_unread_dot(_row(mobile, sid))).to_have_count(0)
    finally:
        mobile_ctx.close()
        if desktop_ctx is not None:
            desktop_ctx.close()


def test_mark_unread_on_desktop_surfaces_on_open_mobile_client(
    playwright: Playwright,
    browser: Browser,
    three_sessions: tuple[str, list[str]],
    mock_llm_server_url: str,
    output_path: str,
) -> None:
    """Marking a read session unread on desktop surfaces it on an open mobile client:
    after both show it read, desktop marks it unread and without a reload the mobile
    surfaces only that session (row, "1 unread" pill, badge 1, dot); a reload agrees."""
    base_url, session_ids = three_sessions
    target = session_ids[0]
    others = session_ids[1:]
    marker = f"xdev-{uuid.uuid4().hex[:8]}"
    # Several copies so a title-generation call can't drain the queue.
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "Task finished: all tests green.", "delay": _REPLY_DELAY_S}] * 3,
        match=marker,
    )
    artifacts = Path(output_path)
    artifacts.mkdir(parents=True, exist_ok=True)

    mobile_ctx = browser.new_context(**playwright.devices["Pixel 7"])
    mobile_ctx.add_init_script(_ANDROID_SHELL_INIT_SCRIPT)
    desktop_ctx: BrowserContext | None = None
    try:
        desktop_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        mobile = mobile_ctx.new_page()
        desktop = desktop_ctx.new_page()
        observer = _ListObserver()
        observer.attach(mobile)

        mobile.goto(f"{base_url}/?sidebar=open")
        desktop.goto(f"{base_url}/inbox")
        for sid in session_ids:
            expect(_row(desktop, sid)).to_be_visible(timeout=30_000)
            expect(_row(mobile, sid)).to_be_visible(timeout=30_000)
            expect(_unread_dot(_row(mobile, sid))).to_have_count(0)
        _wait_badge(mobile, 0, timeout_ms=30_000)

        # Make the target unread on both clients, then read it on desktop so both
        # show it read -- the state that "Mark as unread" must flip back.
        _send_and_leave(desktop, target, f"Finish the task and report. Marker: {marker}")
        expect(_unread_dot(_row(desktop, target))).to_be_visible(timeout=_TURN_TIMEOUT_MS)
        expect(_unread_dot(_row(mobile, target))).to_be_visible(timeout=_TURN_TIMEOUT_MS)
        _wait_badge(mobile, 1, timeout_ms=30_000)

        read_floor = int(time.time())
        _open_session(desktop, target)
        expect(
            desktop.locator('[data-testid="message-bubble"][data-role="assistant"]').last
        ).to_be_visible(timeout=30_000)
        expect(_unread_dot(_row(desktop, target))).to_have_count(0)
        desktop.locator(f'{_SIDEBAR} a[href="/inbox"]').first.click()
        expect(desktop).to_have_url(re.compile(r"/inbox$"))
        expect(desktop.locator(_UNREAD_ROW)).to_have_count(0)

        # Mobile must first catch up to the read so the later unread is a genuine
        # cross-device flip, not a race against the still-unread baseline.
        assert _wait_for_list_refresh(mobile, observer, [target], read_floor), (
            f"mobile never received the desktop read-state: {observer.seen}"
        )
        _wait_badge(mobile, 0, timeout_ms=15_000)
        expect(_unread_dot(_row(mobile, target))).to_have_count(0)
        mobile.screenshot(path=str(artifacts / "mobile-before-desktop-unread.png"))

        # Desktop marks the read session unread from the Inbox sidebar (it is not
        # the open session, so viewing it can't immediately re-mark it read).
        row = _row(desktop, target)
        row.hover()
        row.get_by_test_id("conversation-actions").click()
        desktop.get_by_test_id("mark-unread-conversation").click()
        expect(_unread_dot(row)).to_be_visible()
        expect(desktop.locator(_UNREAD_ROW)).to_have_count(1)
        desktop.screenshot(path=str(artifacts / "desktop-after-unread.png"))

        # The mobile's own list must carry the desktop "Mark as unread" (the
        # server sends it either way), so a surfaced dot can only be the client
        # adopting a strictly-newer revision, not a dropped refresh.
        surfaced = _wait_for_unread_flag(mobile, observer, target)
        print(f"mobile list carried desktop mark-unread: {surfaced} ({observer.unread})")
        mobile.screenshot(path=str(artifacts / "mobile-after-desktop-unread.png"))
        print(f"mobile badge after desktop mark-unread: {_last_badge(mobile)}")
        assert surfaced, f"mobile never received the desktop mark-unread: {observer.unread}"

        _wait_badge(mobile, 1, timeout_ms=15_000)
        expect(_unread_dot(_row(mobile, target))).to_be_visible(timeout=15_000)
        for sid in others:
            expect(_unread_dot(_row(mobile, sid))).to_have_count(0)
        mobile.locator(f'{_SIDEBAR} a[href="/inbox"]').first.tap()
        expect(mobile).to_have_url(re.compile(r"/inbox$"))
        expect(mobile.locator(_UNREAD_ROW)).to_have_count(1)
        expect(mobile.get_by_title("1 unread")).to_be_visible()

        # A reload must still show the cross-device unread: durable, not just live.
        mobile.reload()
        expect(mobile.get_by_role("tab", name="Unread")).to_be_visible(timeout=30_000)
        expect(mobile.locator(_UNREAD_ROW)).to_have_count(1)
        _wait_badge(mobile, 1, timeout_ms=30_000)
    finally:
        mobile_ctx.close()
        if desktop_ctx is not None:
            desktop_ctx.close()
