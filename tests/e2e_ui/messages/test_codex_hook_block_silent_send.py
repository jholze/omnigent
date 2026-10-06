r"""UI journey: a native Codex turn blocked by a UserPromptSubmit hook must not
fail silently on the web Chat view.

A user-authored ``UserPromptSubmit`` command hook that exits non-zero blocks the
turn before any model call. The Terminal/TUI view prints ``Blocked by hook`` and
the hook's stderr; the web Chat view must surface the same failure instead of
echoing the user's message and settling to idle with no error, notice, or reply.

The hook runs even though it is untrusted because runner-owned (web) sessions
launch Codex with ``--dangerously-bypass-hook-trust``. The minimal missing-script
hook here blocks identically to a plugin whose ``UserPromptSubmit`` hook script
is absent.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import (
    _REPO_ROOT,
    _create_native_codex_session,
    _ensure_runner_online,
    _server_state,
    _temp_omnigent_mock_config,
)

from .test_message_render_parity import _USER, _ensure_chat_view, _send
from .test_native_codex_render_parity import (
    _MOCK_TURN_TIMEOUT_MS,
    _open_terminal_view,
    _wait_terminal_connected,
)

# python3 exits with status 2 for a missing script, which Codex treats as a
# blocking hook outcome ("Blocked by hook").
_MISSING_HOOK_SCRIPT = "/tmp/omnigent-missing-hook/activate.py"


def _runner_source_codex_home() -> Path:
    """Return the source ``CODEX_HOME`` the bound runner reads hooks from.

    A workflow-owned runner sets ``CODEX_HOME`` to the prepared env's
    ``codex-config`` dir; a self-spawned runner inherits the ambient value.
    """
    if _server_state.get("workflow_owned"):
        return _REPO_ROOT / ".omnigent" / "repro-env" / "codex-config"
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


@contextlib.contextmanager
def _blocking_user_prompt_submit_hook() -> Iterator[None]:
    """Install a ``UserPromptSubmit`` hook that exits non-zero, then restore."""
    codex_home = _runner_source_codex_home()
    codex_home.mkdir(parents=True, exist_ok=True)
    hooks_path = codex_home / "hooks.json"
    backup = hooks_path.read_bytes() if hooks_path.exists() else None
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python3 {_MISSING_HOOK_SCRIPT}",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    try:
        yield
    finally:
        if backup is None:
            hooks_path.unlink(missing_ok=True)
        else:
            hooks_path.write_bytes(backup)


@pytest.fixture
def hook_blocked_native_codex_session(
    live_server: str,
    mock_llm_server_url: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[str, str]]:
    """A native Codex session whose first turn is blocked by a UserPromptSubmit hook.

    Injects the blocking hook into the runner's source ``CODEX_HOME`` before the
    session launches (the runner symlinks ``hooks.json`` into the session's
    private home at launch), so the hook runs on the first composer send.
    """
    respawned = _ensure_runner_online(live_server, tmp_path_factory)
    runner_id = str(_server_state["runner_id"])
    with (
        _blocking_user_prompt_submit_hook(),
        _temp_omnigent_mock_config(
            mock_llm_server_url, "codex", workflow_owned=bool(_server_state.get("workflow_owned"))
        ),
    ):
        session_id = _create_native_codex_session(live_server, runner_id)
        try:
            yield (live_server, session_id)
        finally:
            httpx.delete(f"{live_server}/v1/sessions/{session_id}", timeout=10.0)
            if respawned is not None:
                respawned.terminate()
                try:
                    respawned.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    respawned.kill()
                    respawned.wait(timeout=5)


@pytest.mark.nightly
@pytest.mark.timeout(300)
def test_native_codex_hook_block_surfaces_error_on_web(
    request: pytest.FixtureRequest,
    hook_blocked_native_codex_session: tuple[str, str],
) -> None:
    """A hook-blocked turn surfaces an error on the web Chat view (not silent)."""
    base_url, session_id = hook_blocked_native_codex_session
    # Create the recorded page only after the non-browser setup above.
    page: Page = request.getfixturevalue("page")

    page.goto(f"{base_url}/c/{session_id}")
    _open_terminal_view(page)
    _wait_terminal_connected(page)
    _ensure_chat_view(page)

    _send(page, "That's a lot of comments we hid, are those all comments we should hide?")

    # The message is accepted and echoed as a user bubble...
    expect(page.locator(_USER)).to_have_count(1, timeout=_MOCK_TURN_TIMEOUT_MS)
    # ...but the UserPromptSubmit hook blocks the turn. The web Chat view must
    # surface the block rather than settling to idle with no error.
    pill = page.get_by_test_id("error-pill").first
    expect(pill).to_be_visible(timeout=_MOCK_TURN_TIMEOUT_MS)
    # The notice carries the hook's own reason, as the Terminal view does.
    content = page.get_by_test_id("error-message-content").first
    if not content.is_visible():
        pill.click()
    expect(content).to_be_visible(timeout=10_000)
    expect(content).to_contain_text("Blocked by hook")
    expect(content).to_contain_text(_MISSING_HOOK_SCRIPT)
