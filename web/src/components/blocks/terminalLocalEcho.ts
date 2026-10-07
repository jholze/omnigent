// Predictive local echo for the web terminal.
//
// When the runner is on a remote host, every keystroke round-trips
// browser -> server -> runner-tunnel -> PTY and back before the typed
// character paints, so typing lags by the full link latency (~0.5-1 s on a
// WAN link). This renders a printable keystroke optimistically at the cursor
// and reconciles it against the authoritative PTY echo when it arrives:
// a confirmed echo is suppressed (no repaint/flicker), and a divergence rolls
// the prediction back so the server output always wins.
//
// Safety is structural. Prediction is confined to a plain line-editing prompt
// (primary screen, no mouse tracking, a single printable ASCII char that will
// not wrap) and is "confidence-gated": the client only predicts after it has
// seen the shell echo a keystroke verbatim, and it drops that confidence on
// every submission or non-printable key. A password prompt is reached by
// submitting a command, so the client is never confident when the first hidden
// character is typed and nothing is drawn locally. A backstop timer rolls back
// and disables prediction if an echo never returns, bounding the exotic case
// where echo is disabled mid-line without a newline.

const PRINTABLE_MIN = 0x20;
const PRINTABLE_MAX = 0x7e;

// Echo round-trip assumed before the link has been measured once.
const DEFAULT_ECHO_RTT_MS = 1000;
// Backstop multiple of the observed echo round-trip, clamped. Comfortably
// above a healthy echo so a correct prediction is never rolled back early,
// while still bounding how long an un-echoed character can linger.
const BACKSTOP_RTT_MULTIPLE = 3;
const BACKSTOP_MIN_MS = 1500;
const BACKSTOP_MAX_MS = 5000;

/** Erase one already-rendered cell and leave the cursor where it started. */
const ERASE_ONE = "\b \b";

/**
 * The slice of xterm the echo logic reads and writes. Narrowed to a handful
 * of members so the reconciliation can be unit-tested without a real terminal.
 */
export interface LocalEchoTerminal {
  write: (data: string | Uint8Array) => void;
  readonly cols: number;
  /** Zero-based cursor column on the active buffer. */
  readonly cursorX: number;
  /** True while an alternate-screen (full-screen TUI) program is active. */
  readonly onAlternateScreen: boolean;
  /** True while the pane program is tracking the mouse. */
  readonly mouseTrackingActive: boolean;
}

export interface LocalEchoOptions {
  /** Monotonic clock, injectable for tests. Defaults to ``performance.now``. */
  now?: () => number;
  /** Timer scheduler, injectable for tests. Defaults to ``setTimeout``. */
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isSinglePrintableAscii(data: string): boolean {
  if (data.length !== 1) return false;
  const code = data.charCodeAt(0);
  return code >= PRINTABLE_MIN && code <= PRINTABLE_MAX;
}

/**
 * Tracks optimistic keystroke echoes and reconciles them with PTY output.
 *
 * ``onInput`` is called for every outbound keystroke (before it is sent) and
 * decides whether to draw it locally. ``reconcile`` is called for every inbound
 * PTY frame and returns the bytes the caller should still write — the confirmed
 * echo of a predicted character is stripped so it is not painted twice.
 */
export class TerminalLocalEcho {
  private readonly term: LocalEchoTerminal;
  private readonly now: () => number;
  private readonly setTimer: (callback: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;

  /** Char codes drawn locally and awaiting their verbatim PTY echo, in order. */
  private pending: number[] = [];
  /** Whether the shell has recently echoed a keystroke verbatim. */
  private confident = false;
  /** A keystroke sent while not yet confident, awaiting its echo, or ``null``. */
  private awaitingEcho: number | null = null;
  private awaitingSentAt = 0;
  /** Last observed echo round-trip, used to size the rollback backstop. */
  private echoRttMs = DEFAULT_ECHO_RTT_MS;
  private backstopHandle: unknown = null;
  private disposed = false;

  constructor(term: LocalEchoTerminal, options: LocalEchoOptions = {}) {
    this.term = term;
    this.now = options.now ?? (() => performance.now());
    this.setTimer = options.setTimer ?? ((cb, ms) => setTimeout(cb, ms));
    this.clearTimer = options.clearTimer ?? ((handle) => clearTimeout(handle as never));
  }

  /**
   * Handle an outbound keystroke. Draws a safe printable character locally and
   * records it for reconciliation; any other input stops prediction so a
   * submission, edit, or control sequence is never echoed speculatively.
   */
  onInput(data: string): void {
    if (this.disposed) return;
    if (!isSinglePrintableAscii(data)) {
      // Enter, backspace, arrows, pastes, control and mouse sequences: never
      // predicted. Keep any outstanding predictions so their echoes still
      // reconcile, but stop making new ones until the shell proves it echoes.
      this.confident = false;
      this.awaitingEcho = null;
      return;
    }
    if (!this.canPredict()) {
      // A printable key we will not draw yet. Remember it so its echo can
      // confirm the shell is echoing and let us predict the next key.
      this.awaitingEcho = data.charCodeAt(0);
      this.awaitingSentAt = this.now();
      return;
    }
    const code = data.charCodeAt(0);
    this.term.write(data);
    const wasEmpty = this.pending.length === 0;
    this.pending.push(code);
    if (wasEmpty) this.armBackstop();
  }

  /**
   * Reconcile an inbound PTY frame against outstanding predictions. Returns the
   * bytes the caller should still write: a confirmed echo prefix is stripped
   * (already on screen), and a divergence rolls predictions back first so the
   * returned authoritative bytes land on a clean line.
   */
  reconcile(bytes: Uint8Array): Uint8Array {
    if (this.disposed || (this.pending.length === 0 && this.awaitingEcho === null)) {
      return bytes;
    }
    if (this.pending.length > 0) {
      let offset = 0;
      while (
        offset < bytes.length &&
        this.pending.length > 0 &&
        bytes[offset] === this.pending[0]
      ) {
        this.pending.shift();
        offset += 1;
      }
      if (this.pending.length === 0) {
        this.clearBackstop();
      } else if (offset < bytes.length) {
        // The frame diverged from what we drew: erase the un-confirmed
        // predictions and let the authoritative remainder repaint the line.
        this.rollbackPending();
        this.confident = false;
      }
      return bytes.subarray(offset);
    }
    // Not predicting yet: a leading echo of the awaited key means the shell is
    // echoing, so future keys can be drawn locally. The byte is left in place
    // for the caller to paint.
    if (this.awaitingEcho !== null && bytes.length > 0 && bytes[0] === this.awaitingEcho) {
      this.confident = true;
      this.echoRttMs = this.now() - this.awaitingSentAt;
    }
    this.awaitingEcho = null;
    return bytes;
  }

  /** Drop prediction state for a key handled outside the data stream. */
  noteNonPrintableInput(): void {
    if (this.disposed) return;
    this.confident = false;
    this.awaitingEcho = null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.clearBackstop();
    this.pending = [];
    this.awaitingEcho = null;
  }

  private canPredict(): boolean {
    return (
      this.confident &&
      !this.term.onAlternateScreen &&
      !this.term.mouseTrackingActive &&
      // Leave the final column alone so a predicted char never wraps to the
      // next row, which would break the backspace-based rollback.
      this.term.cursorX < this.term.cols - 1
    );
  }

  private rollbackPending(): void {
    if (this.pending.length > 0) {
      this.term.write(ERASE_ONE.repeat(this.pending.length));
      this.pending = [];
    }
    this.clearBackstop();
  }

  private armBackstop(): void {
    this.clearBackstop();
    const ms = clamp(this.echoRttMs * BACKSTOP_RTT_MULTIPLE, BACKSTOP_MIN_MS, BACKSTOP_MAX_MS);
    this.backstopHandle = this.setTimer(() => {
      this.backstopHandle = null;
      // An echo never came back (e.g. echo disabled mid-line): undo the
      // speculative characters and stop predicting until the shell echoes again.
      this.rollbackPending();
      this.confident = false;
    }, ms);
  }

  private clearBackstop(): void {
    if (this.backstopHandle !== null) {
      this.clearTimer(this.backstopHandle);
      this.backstopHandle = null;
    }
  }
}
