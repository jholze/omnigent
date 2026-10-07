"""E2E: the embedded web UI after the host's user session expires.

The embedding host (e.g. the Databricks monolith) supplies the fetcher that
carries every API call. When the host user session expires, that fetcher
rejects with ``Fetch request failed due expired user session`` before any HTTP
response exists, so the response-based 401 handling in ``authenticatedFetch``
never runs. The embed is expected to recover with a single reload of the
current page, through which the host renews its session; the sidebar session
list and the Files view must then load again instead of staying on the error.
A host that stays expired after that reload gets no further reload.

The host is a stand-in page that mounts the real embed island (see
``tests/e2e_ui/auth/_embed_host.py``); a real workspace login is not involved.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.auth._embed_host import (
    EMBED_BASENAME,
    EXPIRED_SESSION_MESSAGE,
    build_embed_host,
    embedded_url,
    serve_embed_host,
)
from tests.e2e_ui.conftest import open_right_rail

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEEDED_FILE = "embedded_notes.md"
# Covers React Query's default retries (~7 s) before an error is shown.
_RECOVERY_TIMEOUT_S = 20.0


@pytest.fixture(scope="session")
def embed_host_build() -> None:
    build_embed_host()


def _seed_workspace_file(base_url: str, session_id: str, path: str) -> None:
    resp = httpx.put(
        f"{base_url}/v1/sessions/{session_id}/resources/environments/default/filesystem/{path}",
        json={"content": f"# {path}\n", "encoding": "utf-8"},
        timeout=10.0,
    )
    resp.raise_for_status()


@pytest.fixture
def embedded_session(
    embed_host_build: None,
    seeded_session: tuple[str, str],
    request: pytest.FixtureRequest,
) -> Iterator[tuple[Page, str, str]]:
    """Open the seeded session inside the stand-in host; yields ``(page, base_url, session_id)``.

    The recorded page is requested only after the non-browser setup finished.
    """
    base_url, session_id = seeded_session
    _seed_workspace_file(base_url, session_id, _SEEDED_FILE)
    page: Page = request.getfixturevalue("page")
    serve_embed_host(page, base_url)
    page.goto(embedded_url(base_url, f"/c/{session_id}"))
    try:
        yield page, base_url, session_id
    finally:
        shutil.rmtree(_REPO_ROOT / session_id, ignore_errors=True)


def _sidebar_list(page: Page) -> Locator:
    return page.get_by_test_id("sidebar-conversation-list")


def _sidebar_session_link(page: Page, session_id: str) -> Locator:
    return _sidebar_list(page).locator(f'a[href="{EMBED_BASENAME}/c/{session_id}"]')


def _files_rail(page: Page) -> Locator:
    return page.get_by_role("complementary", name="Workspace")


def _file_row(rail: Locator, name: str) -> Locator:
    return rail.get_by_role("button", name=re.compile(re.escape(name))).filter(has_text=name)


def _open_files_view(page: Page) -> Locator:
    open_right_rail(page)
    rail = _files_rail(page)
    rail.get_by_role("tab", name="Files", exact=True).click()
    return rail


def _wait_for_populated_embed(page: Page, session_id: str) -> Locator:
    """The embedded UI lists the session and shows the seeded workspace file."""
    expect(page.get_by_test_id("embed-host-session-status")).to_contain_text("Host session: valid")
    expect(_sidebar_session_link(page, session_id)).to_be_visible(timeout=30_000)
    rail = _open_files_view(page)
    expect(_file_row(rail, _SEEDED_FILE)).to_be_visible(timeout=30_000)
    return rail


def _expire_host_session(page: Page, *, persist: bool = False) -> None:
    """Expire the host session; ``persist`` keeps it expired across reloads."""
    page.evaluate(f"window.omnigentEmbedHost.expireSession({{persist: {str(persist).lower()}}})")
    expect(page.get_by_test_id("embed-host-session-status")).to_contain_text("EXPIRED")


def _choose_session_filter(page: Page, label: str) -> None:
    page.get_by_test_id("session-filter").click()
    page.get_by_role("menuitemradio", name=label).click()


def _page_loads(page: Page) -> int | None:
    try:
        return int(page.evaluate("window.omnigentEmbedHost.pageLoads()"))
    except PlaywrightError:
        # The document is mid-navigation; the next poll reads the new one.
        return None


def _expect_single_reload_recovery(page: Page, stuck_surface: Locator) -> None:
    """The embed reloads the current URL exactly once after the host session expired."""
    url_before = page.url
    deadline = time.monotonic() + _RECOVERY_TIMEOUT_S
    while time.monotonic() < deadline:
        if _page_loads(page) == 2:
            break
        page.wait_for_timeout(250)
    else:
        shown = stuck_surface.inner_text() if stuck_surface.count() else "<nothing rendered>"
        pytest.fail(
            "embedded UI never reloaded after the host session expired "
            f"(page loads still {_page_loads(page)}); still showing: {shown!r}"
        )
    page.wait_for_load_state()
    assert page.url == url_before
    page.wait_for_timeout(3_000)
    assert _page_loads(page) == 2, "the embed kept reloading after recovering once"


def test_sidebar_session_list_recovers_after_host_session_expires(
    embedded_session: tuple[Page, str, str],
) -> None:
    page, _base_url, session_id = embedded_session
    _wait_for_populated_embed(page, session_id)

    _expire_host_session(page)
    # "Archived sessions" is served by its own query, so choosing it fetches
    # through the host; "My sessions" is derived client-side on a single-user server.
    _choose_session_filter(page, "Archived sessions")

    _expect_single_reload_recovery(page, _sidebar_list(page))
    expect(_sidebar_list(page)).to_contain_text("No sessions", timeout=30_000)
    expect(_sidebar_list(page)).not_to_contain_text("Failed to load")
    _choose_session_filter(page, "All sessions")
    expect(_sidebar_session_link(page, session_id)).to_be_visible(timeout=30_000)


def test_files_view_recovers_after_host_session_expires(
    embedded_session: tuple[Page, str, str],
) -> None:
    page, _base_url, session_id = embedded_session
    rail = _wait_for_populated_embed(page, session_id)

    _expire_host_session(page)
    rail.get_by_role("button", name="Refresh files").click()

    _expect_single_reload_recovery(page, rail)
    rail = _open_files_view(page)
    expect(_file_row(rail, _SEEDED_FILE)).to_be_visible(timeout=30_000)
    expect(rail).not_to_contain_text("Failed to load")


def test_persistently_expired_host_session_reloads_only_once(
    embedded_session: tuple[Page, str, str],
) -> None:
    page, _base_url, session_id = embedded_session
    _wait_for_populated_embed(page, session_id)

    # Models a host whose re-authentication does not succeed on the reload.
    _expire_host_session(page, persist=True)
    _choose_session_filter(page, "Archived sessions")

    _expect_single_reload_recovery(page, _sidebar_list(page))
    expect(page.get_by_test_id("embed-host-session-status")).to_contain_text("EXPIRED")
    # The reloaded page's requests are rejected again; the error is shown instead
    # of another reload.
    expect(_sidebar_list(page)).to_contain_text(
        f"Failed to load: {EXPIRED_SESSION_MESSAGE}", timeout=30_000
    )
    page.wait_for_timeout(5_000)
    assert _page_loads(page) == 2, "the embed reloaded again while the host stayed expired"
