r"""UI journey: a native Hermes session renders parity with its TUI.

The native ``hermes-native`` ("Hermes") wrapper is terminal-first: the real
``hermes`` CLI runs in the session terminal, the SPA's **Terminal** view attaches
to that live TUI over a WebSocket, and the SPA's **Chat** view renders the SAME
canonical transcript the TUI prints. A native forwarder
(:mod:`omnigent.harnesses.hermes_native.forwarder`) tails Hermes' SQLite ``state.db`` and
mirrors the transcript back OUT as conversation items; web-composer messages are
injected INTO the TUI's tmux pane by
:class:`omnigent.inner.hermes_native_executor.HermesNativeExecutor`. This suite is
the hermes sibling of ``test_native_goose_render_parity`` and asserts the same
three properties:

1. **Render parity with the TUI.** Composer turns are sent through the web SPA;
   each per-turn user marker and assistant token must also appear in the
   canonical transcript, in order, exactly once.
2. **A TUI-originated message surfaces in the web UI.** A turn typed directly
   into the Hermes TUI must be mirrored back out as a user item + assistant reply.
3. **No duplicate rendering.** Every marker/token lands in exactly one bubble.

Gating
------
Like goose-native, Hermes authenticates from its own config (``hermes setup`` /
``hermes model`` → ``~/.hermes/config.yaml``), which CI does not provision. The
suite **skips** when ``hermes``/``tmux`` are absent or no Hermes config is
present, and runs for real where Hermes is configured. A live turn also skips
(when the Hermes TUI pane reports no inference provider, an auth failure, or an
unavailable model) so a machine with Hermes but no working provider does not
fail waiting on an assistant reply it can never produce.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from .test_message_render_parity import (
    _ASSISTANT,
    _USER,
    _WORKING,
    _assert_no_duplicate_render,
    _assert_transcript_parity,
    _ensure_chat_view,
    _select_view_mode,
    _send,
    _turn_prompt,
)

_log = logging.getLogger(__name__)

_TERMINAL_VIEW = '[data-testid="terminal-view"]'
_XTERM_INPUT = ".xterm-helper-textarea"

_NATIVE_TURN_TIMEOUT_MS = 180_000
_TERMINAL_READY_TIMEOUT_MS = 120_000
_COMPOSER_TURNS = 2


def _hermes_unavailable_reason() -> str | None:
    """Return a skip reason when the hermes-native prerequisites are absent.

    hermes-native needs the ``hermes`` binary + ``tmux`` on PATH and a usable
    Hermes configuration (``~/.hermes/config.yaml``, or ``$HERMES_HOME``). Any
    missing → a clean skip (CI provisions no Hermes account).

    :returns: A human-readable skip reason, or ``None`` when prerequisites exist.
    """
    import os as _os

    if shutil.which("hermes") is None:
        return "hermes-native render-parity needs the `hermes` binary on PATH."
    if shutil.which("tmux") is None:
        return "hermes-native render-parity needs `tmux` on PATH (runner-owned TUI pane)."
    hermes_home = _os.environ.get("HERMES_HOME")
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    if not (home / "config.yaml").is_file():
        return (
            "hermes-native render-parity needs a Hermes config: run `hermes setup` / "
            "`hermes model`. Skipped (not failed) because CI does not provision a "
            "Hermes account by default."
        )
    return None


pytestmark = pytest.mark.skipif(
    _hermes_unavailable_reason() is not None,
    reason=_hermes_unavailable_reason() or "",
)


def _open_terminal_view(page: Page) -> None:
    """Switch a terminal-first session to its Terminal (TUI) view."""
    expect(page.get_by_test_id("view-mode-toggle")).to_be_visible(
        timeout=_TERMINAL_READY_TIMEOUT_MS
    )
    _select_view_mode(page, "Terminal")


def _wait_terminal_connected(page: Page) -> None:
    """Wait until the embedded xterm has attached to the live Hermes TUI."""
    terminal = page.locator(_TERMINAL_VIEW).last
    expect(terminal).to_have_attribute(
        "data-state", "connected", timeout=_TERMINAL_READY_TIMEOUT_MS
    )


def _type_into_tui(page: Page, text: str) -> None:
    """Type *text* into the embedded Hermes TUI and submit with Enter.

    Hermes' prompt_toolkit REPL submits on Enter, so a single Enter after a short
    settle sends exactly one turn.
    """
    xterm_input = page.locator(_TERMINAL_VIEW).last.locator(_XTERM_INPUT)
    expect(xterm_input).to_be_attached(timeout=30_000)
    xterm_input.focus()
    page.keyboard.type(text, delay=15)
    page.wait_for_timeout(1500)
    page.keyboard.press("Enter")


def _wait_marker_in_transcript(
    base_url: str, session_id: str, marker: str, *, timeout_ms: int
) -> None:
    """Poll the canonical transcript until *marker* appears (TUI turn forwarded)."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        resp = httpx.get(
            f"{base_url}/v1/sessions/{session_id}/items",
            params={"limit": 100, "order": "asc"},
            timeout=10.0,
        )
        if resp.status_code == 200 and any(
            marker in str(item.get("content")) for item in resp.json().get("data", [])
        ):
            return
        time.sleep(2.0)
    raise AssertionError(
        f"marker {marker!r} never reached the transcript within {timeout_ms}ms — "
        f"the TUI-typed turn was not submitted/forwarded for {session_id}."
    )


#: Distinctive no-provider errors Hermes raises at agent init (no API call runs,
#: so there is no "API call failed" anchor to gate them on).
_NO_PROVIDER_SIGNATURES = (
    "no llm provider configured",
    "no api key was found",
)

#: Inference-error report anchors. Auth/model signatures below are only trusted
#: when one of these anchors is also present, so a tool transcript that merely
#: quotes such words can't false-trigger a skip.
_PROVIDER_ERROR_ANCHORS = (
    "api call failed",
    "non-retryable",
)

#: Provider-reported inference failures, grouped by the skip-reason phrase they
#: map to (the reason must name one of these three conditions).
_PROVIDER_ERROR_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "auth failure",
        (
            "third-party apps now draw from extra usage",
            "invalid_request_error",
            "invalid api key",
            "invalid_api_key",
            "authentication",
            "unauthorized",
            "invalid token",
            "token expired",
            "token revoked",
            "insufficient credits",
            "insufficient_quota",
            "payment required",
            "billing hard limit",
            "exceeded your current quota",
        ),
    ),
    (
        "model unavailable",
        (
            "is not a valid model",
            "invalid model",
            "model not found",
            "model_not_found",
            "no such model",
            "unknown model",
            "unsupported model",
            "does not exist",
        ),
    ),
)


def _hermes_provider_error_reason(session_id: str) -> str | None:
    """Return a skip reason when the live Hermes pane reports a provider error.

    The runner launches the Hermes TUI in a runner-owned tmux pane and advertises
    its socket/target under the per-session bridge dir. When inference cannot
    start — no inference provider, an auth failure, or an unavailable model — the
    TUI prints the provider's error into that pane. This captures the pane and
    maps a matching signature to a skip reason, or returns ``None`` when the pane
    reports no provider error (yet).
    """
    from omnigent.harnesses.hermes_native.bridge import (
        bridge_dir_for_session_id,
        capture_hermes_pane,
    )

    pane = capture_hermes_pane(bridge_dir_for_session_id(session_id))
    if not pane:
        return None
    lowered = pane.lower()

    for signature in _NO_PROVIDER_SIGNATURES:
        if signature in lowered:
            return (
                f"hermes-native render-parity skipped: the Hermes pane reported "
                f"no inference provider (matched {signature!r})."
            )

    if not any(anchor in lowered for anchor in _PROVIDER_ERROR_ANCHORS):
        return None
    for category, signatures in _PROVIDER_ERROR_SIGNATURES:
        for signature in signatures:
            if signature in lowered:
                return (
                    f"hermes-native render-parity skipped: the Hermes pane reported "
                    f"{category} (matched {signature!r})."
                )
    return None


def _wait_assistant_token_or_skip(
    page: Page,
    session_id: str,
    assistant_token: str,
    *,
    timeout_ms: int,
) -> None:
    """Wait for the assistant token, skipping when the pane reports a provider error.

    Hermes can be launchable (binary + tmux + config present) yet still produce no
    reply when its inference provider has no working access; the TUI prints the
    provider error while the chat view stays on "Working". Poll the pane alongside
    the token so that condition skips instead of hanging, while a reply that simply
    lacks the token still fails via the timeout below.
    """
    token_bubble = page.locator(_ASSISTANT, has_text=assistant_token).first
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if token_bubble.is_visible():
            return
        reason = _hermes_provider_error_reason(session_id)
        if reason is not None:
            pytest.skip(reason)
        time.sleep(1.0)
    raise AssertionError(
        f"assistant token {assistant_token!r} never appeared within {timeout_ms}ms "
        f"for session {session_id}."
    )


@pytest.mark.timeout(900)
def test_native_hermes_message_render_parity(
    page: Page,
    native_hermes_session: tuple[str, str],
) -> None:
    """Native Hermes renders parity with its TUI, both ways, with no dupes.

    Mirrors ``test_native_goose_message_render_parity``: composer parity (IN), a
    TUI-originated turn surfacing in the web UI (OUT), and no duplicate rendering.
    """
    base_url, session_id = native_hermes_session
    _log.info("native-hermes session ready: base_url=%s session_id=%s", base_url, session_id)

    page.goto(f"{base_url}/c/{session_id}")

    _open_terminal_view(page)
    _wait_terminal_connected(page)
    _log.info("Hermes TUI attached (terminal-view connected)")

    user_markers: list[str] = []
    assistant_tokens: list[str] = []

    def _new_turn(index: int) -> tuple[str, str]:
        nonce = uuid.uuid4().hex[:8]
        user_marker = f"usr-{index}-{nonce}"
        assistant_token = f"ast-{index}-{nonce}"
        user_markers.append(user_marker)
        assistant_tokens.append(assistant_token)
        return user_marker, assistant_token

    # --- Property 1 & 3: composer turns (IN) render parity, no dupes. ---
    _ensure_chat_view(page)
    for index in range(1, _COMPOSER_TURNS + 1):
        user_marker, assistant_token = _new_turn(index)
        _send(page, _turn_prompt(index, user_marker, assistant_token))
        # The user-message mirror is independent of inference: a turn whose user
        # message never reaches the transcript must still fail (not skip).
        expect(page.locator(_USER)).to_have_count(index, timeout=30_000)
        _wait_assistant_token_or_skip(
            page, session_id, assistant_token, timeout_ms=_NATIVE_TURN_TIMEOUT_MS
        )
        expect(page.locator(_WORKING)).to_have_count(0, timeout=_NATIVE_TURN_TIMEOUT_MS)

    # --- Property 2 & 3: a TUI-originated turn (OUT) surfaces in the web UI. ---
    tui_index = _COMPOSER_TURNS + 1
    tui_marker, tui_token = _new_turn(tui_index)
    _open_terminal_view(page)
    _wait_terminal_connected(page)
    _type_into_tui(page, _turn_prompt(tui_index, tui_marker, tui_token))
    _wait_marker_in_transcript(base_url, session_id, tui_token, timeout_ms=_NATIVE_TURN_TIMEOUT_MS)

    _ensure_chat_view(page)
    expect(page.locator(_ASSISTANT, has_text=tui_token).first).to_be_visible(
        timeout=_NATIVE_TURN_TIMEOUT_MS
    )
    expect(page.locator(_USER, has_text=tui_marker).first).to_be_visible(timeout=30_000)
    expect(page.locator(_WORKING)).to_have_count(0, timeout=_NATIVE_TURN_TIMEOUT_MS)
    expect(page.locator(_USER)).to_have_count(len(user_markers), timeout=30_000)

    # --- Assert all three properties over every turn. ---
    _assert_no_duplicate_render(page, user_markers, assistant_tokens)
    _assert_transcript_parity(base_url, session_id, user_markers, assistant_tokens)
    _log.info("all turns verified: render parity + no-duplicate-render + transcript parity")
