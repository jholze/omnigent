/**
 * Approvals in an open Agent-Thread (FR-055 … FR-058).
 *
 * The card is the existing `ApprovalCard`; this module only routes the
 * verdict for the thread's session. Unlike the session chat it does not flip
 * the card before the server answers: a pending card keeps listening for
 * `response.elicitation_resolved`, so a verdict stored elsewhere wins over a
 * stale click (AC-058a). The click still shows "Antwort wird gesendet…" at
 * once (NFR-030). No toast in any outcome; problems show on the card.
 */
import { create } from "zustand";

import type { ApprovalCardLabels } from "@/components/blocks/ApprovalCard";
import { authenticatedFetch } from "@/lib/identity";
import type { ElicitationBlock } from "@/lib/blocks";
import { ApiError, approve } from "@/lib/sessionsApi";
import { conversationRegistry } from "@/store/conversationRegistry";

export const READ_ONLY_NOTE = "Nur Aufrufer und Bearbeiter können antworten";
export const SENDING_NOTE = "Antwort wird gesendet…";
export const SEND_FAILED_TEXT = "Antwort nicht gesendet. Bitte erneut versuchen.";

export const CHAT_APPROVAL_LABELS: ApprovalCardLabels = {
  title: "Freigabe nötig",
  approve: "Genehmigen",
  reject: "Ablehnen",
  approved: "Genehmigt",
  rejected: "Abgelehnt",
  cancelled: "Abgebrochen",
  resolvedElsewhere: "Bereits beantwortet",
  resolvedElsewhereDetail:
    "Diese Freigabe wurde schon an anderer Stelle beantwortet. Das Ergebnis liegt in der Session.",
  byResolver: (verdict, resolver) => `${verdict} von ${resolver}`,
};

export type ResolveOutcome = "resolved" | "already_resolved" | "forbidden" | "failed";

type Verdict = "accept" | "decline";

/** Verdict the server stored for an already resolved card, when it reports one. */
export interface StoredVerdict {
  action: "accept" | "decline" | "cancel";
  /** User id that decided; absent when the server does not know. */
  decidedBy?: string;
}

export interface VerdictResult {
  outcome: ResolveOutcome;
  /** Only with `already_resolved`, from `GET …/elicitations/{id}`. */
  stored?: StoredVerdict;
}

/**
 * `{status: "resolved", verdict, decided_by}` from the elicitation GET.
 * `null` for a bare `{status: "resolved"}` (verdict unknown) or anything else.
 */
export function storedVerdictOf(body: Record<string, unknown>): StoredVerdict | null {
  const verdict = body.verdict;
  if (verdict !== "accept" && verdict !== "decline" && verdict !== "cancel") return null;
  const decidedBy = body.decided_by;
  return {
    action: verdict,
    ...(typeof decidedBy === "string" && decidedBy !== "" ? { decidedBy } : {}),
  };
}

interface ApprovalUiState {
  /** Card id → the verdict being sent. */
  sending: Record<string, Verdict>;
  /** Card id → resolver label, only for answers this tab sent. */
  resolvers: Record<string, string>;
  /** Card ids the server refused for this viewer (403). */
  forbidden: Record<string, true>;
  /** Card ids whose last send failed for a transport or server error. */
  failed: Record<string, true>;
}

export const useThreadApprovals = create<ApprovalUiState>(() => ({
  sending: {},
  resolvers: {},
  forbidden: {},
  failed: {},
}));

function withKey<T>(record: Record<string, T>, key: string, value: T): Record<string, T> {
  return { ...record, [key]: value };
}

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!Object.hasOwn(record, key)) return record;
  return Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));
}

function findBlock(sessionId: string, elicitationId: string): ElicitationBlock | undefined {
  return conversationRegistry
    .peek(sessionId)
    ?.getState()
    .blocks.find(
      (block): block is ElicitationBlock =>
        block.type === "elicitation" && block.elicitationId === elicitationId,
    );
}

/** Set a still-pending card to `response`. A card a server event already settled stays. */
function settlePending(
  sessionId: string,
  elicitationId: string,
  response: NonNullable<ElicitationBlock["response"]>,
): void {
  conversationRegistry.peek(sessionId)?.setState((state) => {
    let changed = false;
    const blocks = state.blocks.map((block) => {
      if (
        block.type !== "elicitation" ||
        block.elicitationId !== elicitationId ||
        block.status !== "pending"
      ) {
        return block;
      }
      changed = true;
      return { ...block, status: "responded" as const, response };
    });
    return changed ? { blocks } : {};
  });
}

/**
 * Ask the server first whether the card is still open, then resolve.
 *
 * `GET …/elicitations/{id}` answers `{"status": "resolved"}` once a verdict
 * exists (plus `verdict` and `decided_by` while the server remembers them),
 * so a stale card does not post a second, contradicting verdict.
 */
export async function sendThreadVerdict(
  targetSessionId: string,
  elicitationId: string,
  action: Verdict,
  content?: Record<string, unknown>,
  meta?: Record<string, unknown>,
): Promise<VerdictResult> {
  const path =
    `/v1/sessions/${encodeURIComponent(targetSessionId)}/elicitations/` +
    encodeURIComponent(elicitationId);
  try {
    const check = await authenticatedFetch(path);
    if (check.status === 403) return { outcome: "forbidden" };
    if (check.ok) {
      const body: unknown = await check.json();
      if (
        typeof body === "object" &&
        body !== null &&
        (body as Record<string, unknown>).status === "resolved"
      ) {
        const stored = storedVerdictOf(body as Record<string, unknown>);
        return stored === null
          ? { outcome: "already_resolved" }
          : { outcome: "already_resolved", stored };
      }
    }
  } catch {
    // The check is a guard only; the resolve below decides.
  }
  try {
    await approve(targetSessionId, elicitationId, {
      action,
      ...(content === undefined ? {} : { content }),
      ...(meta === undefined ? {} : { _meta: meta }),
    });
    return { outcome: "resolved" };
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 403) return { outcome: "forbidden" };
      if (error.status === 404 || error.status === 409 || error.status === 410) {
        return { outcome: "already_resolved" };
      }
    }
    return { outcome: "failed" };
  }
}

/**
 * Card submit for the thread's session. `resolverLabel` is the viewer's own
 * name, shown with this viewer's verdict. A verdict decided elsewhere carries
 * `decidedBy` on the block instead; the view names that user.
 */
export async function submitThreadApproval(
  sessionId: string,
  elicitationId: string,
  action: "accept" | "decline",
  resolverLabel: string,
  content?: Record<string, unknown>,
  meta?: Record<string, unknown>,
): Promise<ResolveOutcome> {
  const ui = useThreadApprovals;
  if (Object.hasOwn(ui.getState().sending, elicitationId)) return "failed";
  const block = findBlock(sessionId, elicitationId);
  if (block === undefined || block.status !== "pending") return "already_resolved";
  ui.setState((state) => ({
    sending: withKey(state.sending, elicitationId, action),
    failed: withoutKey(state.failed, elicitationId),
  }));
  const { outcome, stored } = await sendThreadVerdict(
    block.targetSessionId ?? sessionId,
    elicitationId,
    action,
    content,
    meta,
  );
  if (outcome === "resolved") {
    settlePending(sessionId, elicitationId, {
      action,
      ...(content === undefined ? {} : { content }),
      ...(meta === undefined ? {} : { _meta: meta }),
    });
    // Name the resolver only when the card shows this viewer's own verdict.
    const settled = findBlock(sessionId, elicitationId);
    if (settled?.response?.action === action) {
      ui.setState((state) => ({
        resolvers: withKey(state.resolvers, elicitationId, resolverLabel),
      }));
    }
  } else if (outcome === "already_resolved") {
    // A server event may already carry the stored verdict. Else use the one
    // the GET reported; a bare `{status: "resolved"}` stays neutral.
    settlePending(sessionId, elicitationId, stored ?? { action: "auto_resolved" });
  } else if (outcome === "forbidden") {
    ui.setState((state) => ({ forbidden: withKey(state.forbidden, elicitationId, true) }));
  } else {
    ui.setState((state) => ({ failed: withKey(state.failed, elicitationId, true) }));
  }
  ui.setState((state) => ({ sending: withoutKey(state.sending, elicitationId) }));
  return outcome;
}
