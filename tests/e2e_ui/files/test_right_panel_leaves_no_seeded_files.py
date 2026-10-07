"""E2E: the right-panel shell/file-viewer journey leaves no seeded file behind."""

from __future__ import annotations

from pathlib import Path

import httpx
from playwright.sync_api import Page

from tests.e2e_ui.conftest import _TERMINAL_PANEL_FILE
from tests.e2e_ui.files import test_right_panel as right_panel


def test_right_panel_journey_leaves_no_seeded_files(
    page: Page,
    terminal_session: tuple[str, str],
) -> None:
    """The journey seeds the file under the listing's ``base`` (filesystem API) and in
    the runner's cwd, pytest's (scripted terminal); its cleanup must remove both."""
    base_url, session_id = terminal_session
    listing = httpx.get(
        f"{base_url}/v1/sessions/{session_id}/resources/environments/default/filesystem",
        timeout=10.0,
    )
    listing.raise_for_status()
    seeded_files = (
        Path(listing.json()["base"]) / _TERMINAL_PANEL_FILE,
        Path.cwd() / _TERMINAL_PANEL_FILE,
    )

    try:
        right_panel.test_right_panel_terminals_and_file_viewer(page, terminal_session)
        leftovers = [path for path in seeded_files if path.exists()]
        assert not leftovers, f"seeded files left behind: {leftovers}"
    finally:
        for path in seeded_files:
            path.unlink(missing_ok=True)
