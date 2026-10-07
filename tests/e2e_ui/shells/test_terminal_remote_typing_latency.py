"""E2E for OMNI-11653: terminal typing on a high-latency link. Stand-in for the
macOS-desktop/remote-arca environment (not in CI): same xterm.js terminal with a
~900 ms round-trip injected on the attach socket, identical on both builds."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e_ui.conftest import open_right_rail

# One-way latency injected on the attach WebSocket; ~900 ms round-trip
# reproduces the reporter's ~0.5-1 s per-keystroke lag.
ATTACH_LATENCY_MS = 450
# A keystroke must render well under this; the unfixed build renders at
# ~round-trip latency.
INTERACTIVE_BUDGET_MS = 300

# Delay only /attach sockets, both directions, preserving frame order.
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
    """Prove the PTY shell is at a prompt and echoing before measuring; early
    keystrokes can be swallowed before bash starts, so type a ``touch`` and
    wait (retried) for its file to appear."""
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
    """Type one printable char and block until the cursor advances. Primes
    steady-state typing: the client predicts only after seeing the shell echo a
    key, so the first key after a settled prompt costs a full round-trip."""
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
    """Return milliseconds from a steady-state keystroke to it rendering. Times
    how long the cursor-aligned helper textarea takes to move after one key; the
    timing runs in-page so keydown and render share one clock."""
    page.wait_for_timeout(1_000)  # let any prompt redraw settle
    _await_char_render(page, textarea, "e", timeout_ms=8_000)
    textarea.focus()
    # Bind the observer to this exact textarea (the page may hold several) and
    # capture keydown in the capture phase (xterm's handler stops propagation);
    # anchor start at keydown so prompt settling is not counted as the echo.
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
    """A steady-state keystroke renders within the interactive budget over a
    link. The unfixed build renders only when the remote PTY echo returns
    (~900 ms) and fails this; predictive local echo renders immediately."""
    base_url, session_id = terminal_session
    # Create the recorded page only after session/runner setup, so a recording
    # opens on the terminal view rather than blank setup frames.
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
