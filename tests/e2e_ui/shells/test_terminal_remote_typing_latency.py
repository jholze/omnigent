"""E2E: terminal typing stays responsive when the runner is on a remote host.

Reproduces OMNI-11653 (Linear): with a session whose runner is reached over a
high-latency browser<->runner link, every keystroke in the Terminal view only
appears ~0.5-1 s after it is pressed, because the web terminal had no local
echo -- each character round-trips browser -> server -> runner-tunnel -> tmux
PTY and back before it renders.

Faithful stand-in for the reported environment: the real macOS desktop app
against a genuinely remote arca host is not available in CI, so this drives the
same xterm.js terminal the desktop app embeds and models the remote link by
delaying the real terminal-attach WebSocket's outbound frames and inbound
messages by 450 ms each way (a ~900 ms round-trip). The injection is purely
test-side (an init script that wraps ``window.WebSocket`` for ``/attach``
sockets only) and is identical on the unfixed and fixed builds; only the web
client's echo behavior differs between them.

Observation: xterm renders to a WebGL canvas, so typed text never reaches the
DOM. xterm does keep its hidden ``.xterm-helper-textarea`` aligned to the
cursor cell (it repositions on every cursor move), so the time from the
keydown to that element moving measures when the character actually rendered.
Without local echo the cursor only advances once the remote PTY echo returns
(~900 ms); with local echo it advances immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.conftest import open_right_rail

# Milliseconds of one-way latency injected on the terminal-attach WebSocket.
# ~900 ms round-trip reproduces the reporter's ~0.5-1 s per-keystroke lag.
ATTACH_LATENCY_MS = 450
# Interactive budget: a keystroke must render well under this. The unfixed
# build renders at ~round-trip latency (~900 ms), far above it.
INTERACTIVE_BUDGET_MS = 300

# Wraps window.WebSocket so only terminal-attach sockets are delayed, in both
# directions, without touching the app or any other socket. Message events are
# re-dispatched after the delay; equal-delay timers preserve frame order.
_LATENCY_INIT_SCRIPT = """
(() => {
  const DELAY = %d;
  const proto = WebSocket.prototype;
  const isAttach = (ws) => {
    try { return String(ws.url).indexOf('/attach') !== -1; } catch (e) { return false; }
  };
  const origSend = proto.send;
  proto.send = function (data) {
    if (isAttach(this)) {
      setTimeout(() => { try { origSend.call(this, data); } catch (e) {} }, DELAY);
    } else {
      origSend.call(this, data);
    }
  };
  const origAdd = proto.addEventListener;
  proto.addEventListener = function (type, listener, opts) {
    if (type === 'message' && isAttach(this) && typeof listener === 'function') {
      const wrapped = function (ev) { setTimeout(() => listener.call(this, ev), DELAY); };
      return origAdd.call(this, type, wrapped, opts);
    }
    return origAdd.call(this, type, listener, opts);
  };
})();
""" % ATTACH_LATENCY_MS


def _open_new_shell(page: Page) -> None:
    """Create a shell via the Workspace rail's "Open new" -> Shell menu."""
    open_right_rail(page)
    rail = page.get_by_role("complementary", name="Workspace")
    rail.get_by_role("button", name="Open new").click()
    page.get_by_role("menuitem", name=re.compile("Shell")).click()


def _connected_shell_textarea(page: Page) -> Locator:
    """Open a shell and return its focused xterm input textarea once attached."""
    rail = page.get_by_role("complementary", name="Workspace")
    last_error: AssertionError | None = None
    for _ in range(3):
        _open_new_shell(page)
        terminal_view = rail.get_by_test_id("terminal-view").last
        expect(terminal_view).to_be_visible(timeout=60_000)
        try:
            expect(terminal_view).to_have_attribute("data-state", "connected", timeout=30_000)
        except AssertionError as exc:
            last_error = exc
            rail.get_by_role("button", name=re.compile(r"^Close ")).last.click()
            page.get_by_role("button", name=re.compile("Close")).last.click()
            page.wait_for_timeout(1_000)
            continue
        textarea = terminal_view.locator("textarea.xterm-helper-textarea")
        textarea.focus()
        return textarea
    raise AssertionError(f"shell never connected after 3 attempts: {last_error}")


def _await_shell_ready(page: Page, textarea: Locator, tmp_path: Path) -> None:
    """Prove the PTY shell accepts and executes input before measuring.

    The attach WS connects before bash finishes starting, so early keystrokes
    can be swallowed. Typing a ``touch`` and waiting for its file (retried)
    guarantees the shell is at a prompt and echoing by the time we measure.
    """
    ready = tmp_path / "shell_ready.txt"
    for _ in range(6):
        textarea.focus()
        page.keyboard.press("Control+c")
        page.keyboard.type(f"touch {ready}")
        page.keyboard.press("Enter")
        for _ in range(40):
            if ready.exists():
                return
            page.wait_for_timeout(250)
    raise AssertionError("shell never executed the readiness command; cannot measure echo")


def _await_char_render(page: Page, textarea: Locator, char: str, timeout_ms: int) -> None:
    """Type one printable char and block until this terminal's cursor advances.

    Used to prime steady-state typing: the client only predicts a keystroke
    after it has seen the shell echo one verbatim, so the first key after a
    settled prompt always costs a full round-trip. Waiting for that first key to
    paint guarantees the client is in its confident, predicting state before the
    measured keystroke.
    """
    textarea.focus()
    textarea.evaluate(
        """(ta) => {
          const posStr = () => ta.style.left + '|' + ta.style.top;
          window.__prime = { start: posStr(), moved: false };
          const tick = () => {
            if (window.__prime.moved) return;
            if (posStr() !== window.__prime.start) {
              window.__prime.moved = true;
              return;
            }
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }"""
    )
    page.keyboard.type(char)
    page.wait_for_function("() => window.__prime && window.__prime.moved", timeout=timeout_ms)


def _measure_echo_latency_ms(page: Page, textarea: Locator) -> float:
    """Return milliseconds from a steady-state keystroke to it rendering.

    Records this terminal's cursor-aligned helper textarea position, types one
    printable character, and times how long until that position changes. The
    timing runs in the page so the keydown and the render are read off one clock.
    """
    page.wait_for_timeout(1_000)  # let any prompt redraw settle
    # Prime one keystroke so the client is confidently predicting (see
    # _await_char_render); this primer itself still costs a full round-trip.
    _await_char_render(page, textarea, "e", timeout_ms=8_000)
    textarea.focus()
    # Bind the cursor observer to this exact textarea element: the page can hold
    # more than one terminal view, so a document-wide query might watch a
    # different terminal's cursor than the one that receives the keystroke.
    # Capture the keydown on the document in the capture phase -- xterm's own
    # keydown handler on the textarea stops propagation, so a bubble-phase
    # listener there would never fire. Anchor the start position at keydown so
    # any residual prompt settling is not mistaken for the echo.
    textarea.evaluate(
        """(ta) => {
          const posStr = () => ta.style.left + '|' + ta.style.top;
          window.__echo = { keyAt: null, moveAt: null, start: null };
          document.addEventListener('keydown', () => {
            if (window.__echo.keyAt !== null) return;
            window.__echo.keyAt = performance.now();
            window.__echo.start = posStr();
            const tick = () => {
              if (window.__echo.moveAt !== null) return;
              if (posStr() !== window.__echo.start) {
                window.__echo.moveAt = performance.now();
                return;
              }
              requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
          }, true);
        }"""
    )
    page.keyboard.type("x")
    page.wait_for_function(
        """() => window.__echo && (window.__echo.moveAt !== null ||
             (window.__echo.keyAt !== null && performance.now() - window.__echo.keyAt > 5000))""",
        timeout=15_000,
    )
    echo = page.evaluate("() => window.__echo")
    assert echo["keyAt"] is not None, "keydown was never observed on the terminal textarea"
    assert echo["moveAt"] is not None, (
        "the typed character never rendered within 5 s "
        f"(cursor did not advance from {echo['start']!r})"
    )
    return float(echo["moveAt"] - echo["keyAt"])


def test_remote_terminal_typing_echoes_within_interactive_budget(
    request: pytest.FixtureRequest, terminal_session: tuple[str, str], tmp_path: Path
) -> None:
    """A steady-state keystroke renders within the interactive budget over a link.

    Journey: open a shell on a session whose terminal-attach WebSocket has a
    ~900 ms round-trip injected (stand-in for a remote host), prime one keystroke
    so the client is confidently predicting, then type a character and assert it
    appears well under ``INTERACTIVE_BUDGET_MS``. The unfixed build has no local
    echo, so every character renders only once the remote PTY echo returns
    (~900 ms) and this fails; predictive local echo renders it immediately.

    Stand-in: CI cannot run the real macOS desktop app against a remote arca
    host, so this drives the same embedded xterm.js terminal over an injected
    high-latency attach socket. The injection is identical on both builds; only
    the client's echo behavior differs.
    """
    base_url, session_id = terminal_session
    # Create the recorded page only after the session/runner setup above, so a
    # recording of this journey opens on the terminal view rather than on blank
    # setup frames.
    page = request.getfixturevalue("page")
    page.add_init_script(_LATENCY_INIT_SCRIPT)
    page.goto(f"{base_url}/c/{session_id}")
    textarea = _connected_shell_textarea(page)
    _await_shell_ready(page, textarea, tmp_path)

    latency_ms = _measure_echo_latency_ms(page, textarea)
    assert latency_ms < INTERACTIVE_BUDGET_MS, (
        f"keystroke rendered after {latency_ms:.0f} ms over a "
        f"{2 * ATTACH_LATENCY_MS} ms round-trip link; the terminal has no local "
        f"echo, so each character waits for the remote PTY echo (budget "
        f"{INTERACTIVE_BUDGET_MS} ms)"
    )
