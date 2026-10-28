/**
 * Agent controls of an open Agent-Thread (FR-049 … FR-051, FR-042).
 *
 * One controller feeds the toolbar buttons, the composer's slash commands
 * and the run-stuck banner, so rights, pending state and errors agree
 * everywhere. Commands map onto existing endpoints only:
 * - interrupt / retry / stop: `POST /v1/sessions/{id}/events` with
 *   `interrupt`, `retry_session`, `stop_session`;
 * - dismiss: `POST /v1/chat/threads/{root}/dismiss` (T-104).
 *
 * Rights (FR-050): EDIT on the session for interrupt, retry and dismiss;
 * OWNER for stop. The server gates them too; the UI only explains early.
 */
import { useCallback, useState } from "react";

import { useSession } from "@/hooks/useSession";
import { LEVEL_OWNER } from "@/lib/permissionsApi";
import { interrupt as interruptSession, retrySession, stopSession } from "@/lib/sessionsApi";
import { dismissAgentThread, type DirectoryEntry } from "../chatApi";
import { useAgentThreadStore } from "./agentThreadStore";
import { sessionAccessOf } from "./sessionAccess";

export type AgentCommand = "interrupt" | "retry" | "stop" | "dismiss";

export const AGENT_COMMANDS: readonly AgentCommand[] = ["interrupt", "retry", "stop", "dismiss"];

/** Button labels (German UI); slash commands stay English (FR-049). */
export const AGENT_COMMAND_LABELS: Record<AgentCommand, string> = {
  interrupt: "Unterbrechen",
  retry: "Wiederholen",
  stop: "Stoppen",
  dismiss: "Entlassen",
};

export const AGENT_SLASH: Record<AgentCommand, string> = {
  interrupt: "/interrupt",
  retry: "/retry",
  stop: "/stop",
  dismiss: "/dismiss",
};

/** FR-050 hint for interrupt, retry and dismiss (AC-050a, verbatim). */
export const NO_EDIT_HINT = "Keine Berechtigung: nur Aufrufer und Bearbeiter";

/** FR-051 hint for stop (AC-051a). */
export function stopOwnerHint(owner: string | null): string {
  return owner ? `Nur ${owner} (Owner) kann stoppen` : "Nur der Owner der Session kann stoppen";
}

/**
 * The agent command a composer body names, or null. Only the bare command
 * counts (`/retry`, any case, surrounding blanks); everything else, also
 * `/unbekannt`, is a normal message (AC-049b).
 */
export function parseAgentCommand(body: string): AgentCommand | null {
  const word = body.trim().toLowerCase();
  for (const command of AGENT_COMMANDS) {
    if (AGENT_SLASH[command] === word) return command;
  }
  return null;
}

export interface AgentRights {
  edit: boolean;
  owner: boolean;
}

/** Why `command` is not allowed for these rights, or null when it is. */
export function commandBlockReason(
  command: AgentCommand,
  rights: AgentRights,
  ownerLabel: string | null,
): string | null {
  if (command === "stop") return rights.owner ? null : stopOwnerHint(ownerLabel);
  return rights.edit ? null : NO_EDIT_HINT;
}

/** Outcome of a command request from a button or the composer. */
export type CommandRequest =
  { kind: "sent" } | { kind: "confirm" } | { kind: "blocked"; hint: string };

export interface AgentControlsController {
  sessionId: string | null;
  rights: AgentRights;
  ownerLabel: string | null;
  /** `chat_threads.agent_state` is `dismissed`: replies no longer reach the agent. */
  dismissed: boolean;
  pending: Partial<Record<AgentCommand, boolean>>;
  /** Last failed command, shown next to the toolbar. */
  error: { command: AgentCommand; text: string } | null;
  stopConfirmOpen: boolean;
  setStopConfirmOpen: (open: boolean) => void;
  /** Check rights, then send (stop asks for confirmation first). */
  request: (command: AgentCommand) => CommandRequest;
  /** Send `stop_session` after the confirmation. */
  confirmStop: () => void;
  blockReason: (command: AgentCommand) => string | null;
}

function failureText(command: AgentCommand, error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return command === "stop" ? stopOwnerHint(null) : NO_EDIT_HINT;
  return `${AGENT_COMMAND_LABELS[command]} fehlgeschlagen. Bitte erneut versuchen.`;
}

export interface UseAgentControlsInput {
  /** `null`: the thread has no agent session (yet); every command is refused. */
  sessionId: string | null;
  rootId: string;
  directory: Map<string, DirectoryEntry>;
  /** Thread starter, for the stop hint when the session snapshot is not readable. */
  ownerFallback: string | null;
}

export function useAgentControls({
  sessionId,
  rootId,
  ownerFallback,
}: UseAgentControlsInput): AgentControlsController {
  const { session, isLoading, error: sessionError } = useSession(sessionId);
  const access = sessionAccessOf(session, isLoading, sessionError);
  const level = session?.permissionLevel;
  const rights: AgentRights = {
    edit: access === "edit",
    // `null`: permissions are off on this server, everyone may act.
    owner: session !== null && (level === null || (level ?? 0) >= LEVEL_OWNER),
  };
  const ownerLabel = ownerFallback;
  const dismissed = useAgentThreadStore(
    (state) => state.threads[rootId]?.agentState === "dismissed",
  );
  const setAgentState = useAgentThreadStore((state) => state.setAgentState);

  const [pending, setPending] = useState<Partial<Record<AgentCommand, boolean>>>({});
  const [error, setError] = useState<AgentControlsController["error"]>(null);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);

  const blockReason = (command: AgentCommand) => commandBlockReason(command, rights, ownerLabel);

  const send = useCallback(
    (command: AgentCommand) => {
      if (sessionId === null) return;
      setError(null);
      setPending((current) => ({ ...current, [command]: true }));
      let call: Promise<unknown>;
      if (command === "interrupt") call = interruptSession(sessionId);
      else if (command === "retry") call = retrySession(sessionId);
      else if (command === "stop") call = stopSession(sessionId);
      else {
        call = dismissAgentThread(rootId).then((result) =>
          setAgentState(rootId, result.agent_state),
        );
      }
      call
        .catch((cause: unknown) => setError({ command, text: failureText(command, cause) }))
        .finally(() => setPending((current) => ({ ...current, [command]: false })));
    },
    [sessionId, rootId, setAgentState],
  );

  const request = (command: AgentCommand): CommandRequest => {
    const hint = blockReason(command);
    if (hint !== null) return { kind: "blocked", hint };
    if (pending[command]) return { kind: "sent" };
    if (command === "stop") {
      setStopConfirmOpen(true);
      return { kind: "confirm" };
    }
    send(command);
    return { kind: "sent" };
  };

  const confirmStop = () => {
    setStopConfirmOpen(false);
    if (blockReason("stop") !== null || pending.stop) return;
    send("stop");
  };

  return {
    sessionId,
    rights,
    ownerLabel,
    dismissed,
    pending,
    error,
    stopConfirmOpen,
    setStopConfirmOpen,
    request,
    confirmStop,
    blockReason,
  };
}
