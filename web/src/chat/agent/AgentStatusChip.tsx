/**
 * Agent status chip (FR-044, FR-047): lucide icon plus the verbatim label,
 * never colour alone. `braucht Antwort` is the one accent in the chat.
 *
 * The chip's text is exactly the label (E2E reads it); callers give screen
 * readers the "Agent" context next to it.
 *
 * The chip keeps the width of its longest label ("arbeitet im Hintergrund")
 * through an invisible CSS sizer, so a status change does not move the row.
 * The sizer text is a pseudo-element: it is not part of the chip's text
 * content. On a narrow row the chip is capped at the row width and the label
 * truncates, so the sizer never forces horizontal scroll.
 * "arbeitet im Hintergrund" shares the icon and border of "arbeitet"; only
 * "braucht Antwort" (an open approval) gets the warning accent.
 * No motion yet; the crossfade waits for the motion tokens (T-117).
 */
import {
  BotIcon,
  CircleCheckIcon,
  CircleSlashIcon,
  CircleXIcon,
  LoaderCircleIcon,
  LogOutIcon,
  MessageCircleQuestionMarkIcon,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { CHAT_TEST_IDS } from "../testids";
import { AGENT_CHIP_LABELS, type AgentChipKind, type AgentChipState } from "./agentStatus";
import { useAgentThreadChip } from "./useAgentThreadChip";

const ICONS: Record<AgentChipKind, LucideIcon> = {
  launching: LoaderCircleIcon,
  running: LoaderCircleIcon,
  background: LoaderCircleIcon,
  waiting: MessageCircleQuestionMarkIcon,
  idle: CircleCheckIcon,
  failed: CircleXIcon,
  startFailed: CircleXIcon,
  dismissed: LogOutIcon,
  active: BotIcon,
  closed: CircleSlashIcon,
};

const TONES: Record<AgentChipKind, string> = {
  launching: "border-border text-muted-foreground",
  running: "border-border text-foreground",
  background: "border-border text-foreground",
  waiting: "border-warning/50 bg-warning/15 text-foreground",
  idle: "border-border text-muted-foreground",
  failed: "border-destructive/40 text-destructive",
  startFailed: "border-destructive/40 text-destructive",
  dismissed: "border-border text-muted-foreground",
  active: "border-border text-muted-foreground",
  closed: "border-border text-muted-foreground",
};

const pendingFormat = new Intl.PluralRules("de-DE");

/** Tooltip text: what the chip is based on, plus the approval count. */
export function agentChipTitle(state: AgentChipState): string {
  if (state.source === "thread") {
    return "Letzter bekannter Thread-Zustand. Den Live-Status sieht nur, wer die Session lesen darf.";
  }
  if (state.kind === "waiting" && state.pending > 0) {
    const noun =
      pendingFormat.select(state.pending) === "one" ? "Freigabe offen" : "Freigaben offen";
    return `Agent-Status: braucht Antwort, ${state.pending} ${noun}`;
  }
  return `Agent-Status: ${AGENT_CHIP_LABELS[state.kind]}`;
}

export interface AgentStatusChipProps {
  state: AgentChipState;
  className?: string;
}

export function AgentStatusChip({ state, className }: AgentStatusChipProps) {
  const Icon = ICONS[state.kind];
  return (
    <span
      data-testid={CHAT_TEST_IDS.agentStatusChip}
      data-state={state.kind}
      data-source={state.source}
      title={agentChipTitle(state)}
      className={cn(
        "inline-flex h-5 max-w-full shrink-0 items-center gap-1 rounded-sm border px-1.5 text-xs font-medium",
        TONES[state.kind],
        className,
      )}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="inline-grid min-w-0 grid-cols-1 overflow-hidden text-left">
        {/* Sizer text mirrors AGENT_CHIP_WIDEST_LABEL (Tailwind needs a literal). */}
        <span
          aria-hidden="true"
          className="invisible col-start-1 row-start-1 truncate before:content-['arbeitet_im_Hintergrund']"
        />
        <span className="col-start-1 row-start-1 truncate">{AGENT_CHIP_LABELS[state.kind]}</span>
      </span>
    </span>
  );
}

/**
 * Compact chip on an Agent-Thread root in the channel list (FR-047). Renders
 * nothing for a plain thread. Screen readers hear "Agent-Thread" first.
 */
export function AgentRootChip({ rootId }: { rootId: string }) {
  const state = useAgentThreadChip(rootId);
  if (state === null) return null;
  return (
    <span className="inline-flex max-w-full min-w-0 items-center">
      <span className="sr-only">Agent-Thread:</span>
      <AgentStatusChip state={state} />
    </span>
  );
}

/**
 * Chip in the open thread's header (FR-044). Status changes are announced
 * politely; the first value is not.
 */
export function AgentThreadStatus({ rootId }: { rootId: string }) {
  const state = useAgentThreadChip(rootId);
  if (state === null) return null;
  return (
    <span role="status" className="inline-flex min-w-0 items-center">
      <span className="sr-only">Agent:</span>
      <AgentStatusChip state={state} />
    </span>
  );
}
