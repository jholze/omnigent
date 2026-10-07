// Unit tests for the terminal's predictive local echo.
//
// The reconciliation logic is isolated from a real xterm via LocalEchoTerminal,
// so these drive the full predict -> confirm / diverge / backstop paths with a
// fake terminal, an injected clock, and a controllable backstop timer.

import { describe, expect, it } from "vitest";
import {
  type LocalEchoOptions,
  type LocalEchoTerminal,
  TerminalLocalEcho,
} from "./terminalLocalEcho";

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const bytes = (s: string): Uint8Array => encoder.encode(s);
const text = (u: Uint8Array): string => decoder.decode(u);

class FakeTerm implements LocalEchoTerminal {
  writes: string[] = [];
  cols = 80;
  cursorX = 0;
  onAlternateScreen = false;
  mouseTrackingActive = false;

  write(data: string | Uint8Array): void {
    this.writes.push(typeof data === "string" ? data : text(data));
  }

  /** Concatenated local writes, i.e. what the user would see drawn locally. */
  get drawn(): string {
    return this.writes.join("");
  }
}

interface Harness {
  echo: TerminalLocalEcho;
  term: FakeTerm;
  setClock: (ms: number) => void;
  fireBackstop: () => void;
  hasBackstop: () => boolean;
}

function makeHarness(): Harness {
  const term = new FakeTerm();
  let clock = 0;
  let timerCb: (() => void) | null = null;
  const options: LocalEchoOptions = {
    now: () => clock,
    setTimer: (cb) => {
      timerCb = cb;
      return 1;
    },
    clearTimer: () => {
      timerCb = null;
    },
  };
  const echo = new TerminalLocalEcho(term, options);
  return {
    echo,
    term,
    setClock: (ms) => {
      clock = ms;
    },
    fireBackstop: () => {
      const cb = timerCb;
      if (!cb) throw new Error("no backstop scheduled");
      cb();
    },
    hasBackstop: () => timerCb !== null,
  };
}

/** Type a key and feed back its verbatim echo so the shell is "confident". */
function becomeConfident(h: Harness, ch = "a"): void {
  h.echo.onInput(ch);
  h.echo.reconcile(bytes(ch));
  h.term.writes = [];
}

describe("TerminalLocalEcho", () => {
  it("does not draw the first key until the shell has echoed one", () => {
    const h = makeHarness();
    h.echo.onInput("l");
    expect(h.term.drawn).toBe("");

    // The echo confirms the shell echoes; the byte is passed through to paint.
    const out = h.echo.reconcile(bytes("l"));
    expect(text(out)).toBe("l");

    // The next key is now drawn locally, ahead of its echo.
    h.echo.onInput("s");
    expect(h.term.drawn).toBe("s");
  });

  it("strips the confirmed echo so a predicted key is not painted twice", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("x");
    expect(h.term.drawn).toBe("x");

    const out = h.echo.reconcile(bytes("x"));
    expect(text(out)).toBe("");
    expect(h.term.drawn).toBe("x");
  });

  it("confirms predictions across split echo frames", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("a");
    h.echo.onInput("b");
    expect(h.term.drawn).toBe("ab");

    expect(text(h.echo.reconcile(bytes("a")))).toBe("");
    expect(text(h.echo.reconcile(bytes("b")))).toBe("");
  });

  it("passes through trailing output once predictions are confirmed", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("a");
    const out = h.echo.reconcile(bytes("a\r\n$ "));
    expect(text(out)).toBe("\r\n$ ");
  });

  it("rolls back and yields to the server when output diverges", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("a");
    h.term.writes = [];

    const out = h.echo.reconcile(bytes("DONE\n"));
    // The speculative "a" is erased, then the authoritative bytes are returned.
    expect(h.term.drawn).toBe("\b \b");
    expect(text(out)).toBe("DONE\n");

    // Confidence dropped: the next key is not drawn until re-confirmed.
    h.echo.onInput("b");
    expect(h.term.writes.join("")).toBe("\b \b");
  });

  it("keeps pending predictions reconciling after a submission", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("a");
    h.echo.onInput("b");
    h.term.writes = [];

    // Enter is never predicted, but the already-drawn chars must still strip.
    h.echo.onInput("\r");
    const out = h.echo.reconcile(bytes("ab\r\n$ "));
    expect(text(out)).toBe("\r\n$ ");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict after a submission even when it was confident", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("\r");
    h.term.writes = [];

    // Models a password prompt reached by running a command: nothing is drawn.
    h.echo.onInput("p");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict a non-printable action key", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("\x7f");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict on the alternate screen", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.term.onAlternateScreen = true;
    h.echo.onInput("x");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict while the pane tracks the mouse", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.term.mouseTrackingActive = true;
    h.echo.onInput("x");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict at the right edge to avoid a wrap", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.term.cursorX = h.term.cols - 1;
    h.echo.onInput("x");
    expect(h.term.drawn).toBe("");
  });

  it("does not predict pastes or non-ASCII input", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("hello");
    h.echo.onInput("é");
    expect(h.term.drawn).toBe("");
  });

  it("rolls back and stops predicting when an echo never returns", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("x");
    h.term.writes = [];
    expect(h.hasBackstop()).toBe(true);

    h.fireBackstop();
    expect(h.term.drawn).toBe("\b \b");

    h.echo.onInput("y");
    expect(h.term.writes.join("")).toBe("\b \b");
  });

  it("clears the backstop once a prediction is confirmed", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.onInput("x");
    expect(h.hasBackstop()).toBe(true);
    h.echo.reconcile(bytes("x"));
    expect(h.hasBackstop()).toBe(false);
  });

  it("ignores input and output after dispose", () => {
    const h = makeHarness();
    becomeConfident(h);
    h.echo.dispose();
    h.echo.onInput("x");
    expect(h.term.drawn).toBe("");
    const out = h.echo.reconcile(bytes("x"));
    expect(text(out)).toBe("x");
  });
});
