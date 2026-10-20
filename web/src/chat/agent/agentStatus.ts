/**
 * Status mapping for the Agent-Thread chip (FR-044, FR-047, 03-domain.md).
 *
 * Session status wins when the viewer can see it. Without it (no grant, or
 * nothing streamed yet) the chip falls back to the last known
 * `chat_threads.agent_state` (AC-047b).
 */

export type AgentChipKind =
  | "launching"
  | "running"
  | "background"
  | "waiting"
  | "idle"
  | "failed"
  | "startFailed"
  | "dismissed"
  | "active"
  | "closed";

/** Where the chip value came from: the live session, or `chat_threads`. */
export type AgentChipSource = "session" | "thread";

export interface AgentChipState {
  kind: AgentChipKind;
  source: AgentChipSource;
  /** Open approvals, when the session reports a count. */
  pending: number;
}

/**
 * Labels. `waiting` ("braucht Antwort") means an approval is open. A session
 * that waits with no open approval works in the background. `active`,
 * `startFailed` and `closed` only show the `chat_threads` state for a viewer
 * without session access; `failed` ("Fehler") stays the live session failure.
 */
export const AGENT_CHIP_LABELS: Record<AgentChipKind, string> = {
  launching: "startet…",
  running: "arbeitet",
  background: "arbeitet im Hintergrund",
  waiting: "braucht Antwort",
  idle: "fertig",
  failed: "Fehler",
  startFailed: "Start fehlgeschlagen",
  dismissed: "entlassen",
  active: "aktiv",
  closed: "beendet",
};

/** Longest label: the chip reserves its width so a status change does not move the row. */
export const AGENT_CHIP_WIDEST_LABEL = "arbeitet im Hintergrund";

const SESSION_KINDS: Record<string, AgentChipKind> = {
  launching: "launching",
  running: "running",
  waiting: "waiting",
  idle: "idle",
  failed: "failed",
};

const THREAD_KINDS: Record<string, AgentChipKind> = {
  starting: "launching",
  active: "active",
  start_failed: "startFailed",
  dismissed: "dismissed",
  closed: "closed",
};

export interface AgentChipInput {
  /** `chat_threads.agent_state`, if known. */
  agentState?: string | null;
  /** `SessionStatusEvent.status` or `SessionListItem.status`, if the viewer has it. */
  sessionStatus?: string | null;
  /** `pending_elicitations_count`, or open cards in the live stream. */
  pendingElicitations?: number;
}

/**
 * An open approval is what makes the session need someone. `waiting` alone
 * (e.g. a background task) is not. The session list folds `waiting` into
 * `running`, so a running row with an open approval also needs an answer.
 */
function sessionChipKind(kind: AgentChipKind, pending: number): AgentChipKind {
  if (kind !== "running" && kind !== "waiting") return kind;
  if (pending > 0) return "waiting";
  return kind === "waiting" ? "background" : "running";
}

/** `null` means: not an Agent-Thread (or nothing known yet), render no chip. */
export function agentChipState({
  agentState = null,
  sessionStatus = null,
  pendingElicitations = 0,
}: AgentChipInput): AgentChipState | null {
  const pending = Number.isFinite(pendingElicitations) ? Math.max(0, pendingElicitations) : 0;
  // A dismissed agent no longer follows the thread, whatever its session does.
  if (agentState === "dismissed" || agentState === "closed") {
    return { kind: THREAD_KINDS[agentState], source: "thread", pending: 0 };
  }
  const sessionKind = sessionStatus ? SESSION_KINDS[sessionStatus] : undefined;
  if (sessionKind !== undefined) {
    return { kind: sessionChipKind(sessionKind, pending), source: "session", pending };
  }
  const threadKind = agentState ? THREAD_KINDS[agentState] : undefined;
  if (threadKind === undefined) return null;
  return { kind: threadKind, source: "thread", pending: 0 };
}
