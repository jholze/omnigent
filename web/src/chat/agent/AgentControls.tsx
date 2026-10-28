/**
 * Agent toolbar above the thread composer (FR-049 … FR-051, FR-042).
 *
 * Quiet text buttons, one row: Unterbrechen, Wiederholen, Stoppen,
 * Entlassen. A control the viewer may not use stays visible with
 * `aria-disabled`, a tooltip naming the required role and, on click or tap
 * (touch has no hover), the same hint as one status line under the row.
 * Stop is irreversible and asks first (INT-04).
 */
import { useState, type ReactNode } from "react";
import { BotIcon, CircleAlertIcon, LockIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { CHAT_TEST_IDS } from "../testids";
import {
  AGENT_COMMAND_LABELS,
  AGENT_COMMANDS,
  AGENT_SLASH,
  type AgentCommand,
  type AgentControlsController,
} from "./useAgentControls";

const COMMAND_TEST_IDS: Record<AgentCommand, string> = {
  interrupt: CHAT_TEST_IDS.agentControlInterrupt,
  retry: CHAT_TEST_IDS.agentControlRetry,
  stop: CHAT_TEST_IDS.agentControlStop,
  dismiss: CHAT_TEST_IDS.agentControlDismiss,
};

export interface AgentCommandButtonProps {
  controller: AgentControlsController;
  command: AgentCommand;
  /** A blocked click shows its hint here (touch path to the tooltip text). */
  onBlocked: (hint: string) => void;
  variant?: "ghost" | "outline";
  testId?: string;
  className?: string;
  children?: ReactNode;
}

/**
 * One agent command. Blocked: `aria-disabled` (stays focusable, so the
 * tooltip is reachable by keyboard) instead of `disabled`.
 */
export function AgentCommandButton({
  controller,
  command,
  onBlocked,
  variant = "ghost",
  testId,
  className,
  children,
}: AgentCommandButtonProps) {
  const hint = controller.blockReason(command);
  const blocked = hint !== null;
  const busy = Boolean(controller.pending[command]);
  const label = AGENT_COMMAND_LABELS[command];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={variant}
          size="sm"
          componentId={`chat.agent.control.${command}`}
          data-testid={testId ?? COMMAND_TEST_IDS[command]}
          data-blocked={blocked || undefined}
          aria-disabled={blocked || busy || undefined}
          aria-busy={busy || undefined}
          className={cn(
            // NFR-032: 44 px touch target on every viewport for these controls.
            "min-h-11 px-2 text-xs font-medium",
            variant === "ghost" && "text-muted-foreground hover:text-foreground",
            blocked &&
              "cursor-not-allowed opacity-60 hover:bg-transparent hover:text-muted-foreground",
            busy && "cursor-progress",
            className,
          )}
          onClick={() => {
            if (blocked) {
              onBlocked(hint);
              return;
            }
            if (busy) return;
            controller.request(command);
          }}
        >
          {children}
          {busy ? `${label}…` : label}
        </Button>
      </TooltipTrigger>
      <TooltipContent
        data-testid={blocked ? CHAT_TEST_IDS.agentControlTooltip : undefined}
        shortcut={blocked ? undefined : [AGENT_SLASH[command]]}
      >
        {blocked ? hint : label}
      </TooltipContent>
    </Tooltip>
  );
}

export function StopConfirmDialog({ controller }: { controller: AgentControlsController }) {
  return (
    <Dialog open={controller.stopConfirmOpen} onOpenChange={controller.setStopConfirmOpen}>
      <DialogContent data-testid={CHAT_TEST_IDS.agentStopConfirm} showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Session stoppen?</DialogTitle>
          <DialogDescription>
            Der Agent hört sofort auf. Der Verlauf bleibt lesbar, die Session lässt sich danach
            nicht fortsetzen.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            className="max-md:min-h-11"
            onClick={() => controller.setStopConfirmOpen(false)}
          >
            Abbrechen
          </Button>
          <Button
            type="button"
            variant="destructive"
            componentId="chat.agent.control.stop.confirm"
            data-testid={CHAT_TEST_IDS.agentStopConfirmButton}
            className="max-md:min-h-11"
            onClick={controller.confirmStop}
          >
            Session stoppen
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export interface AgentControlsProps {
  controller: AgentControlsController;
  className?: string;
}

export function AgentControls({ controller, className }: AgentControlsProps) {
  const [hint, setHint] = useState<string | null>(null);
  const readOnly = !controller.rights.edit;
  // A dismissed agent has nothing left to dismiss; the chip says "entlassen".
  const commands = AGENT_COMMANDS.filter(
    (command) => command !== "dismiss" || !controller.dismissed,
  );
  const message = controller.error?.text ?? hint;
  return (
    // Sits inside the composer wrapper, so it shares the card's left edge.
    <div className={cn("shrink-0", className)}>
      <div
        role="toolbar"
        aria-label="Agent steuern"
        data-testid={CHAT_TEST_IDS.agentControls}
        className="flex min-w-0 flex-wrap items-center gap-x-0.5"
      >
        <span
          className="flex size-6 shrink-0 items-center justify-center text-muted-foreground"
          title={readOnly ? "Nur lesen: Steuerung für Aufrufer und Bearbeiter" : undefined}
        >
          {readOnly ? (
            <LockIcon className="size-4" aria-hidden="true" />
          ) : (
            <BotIcon className="size-4" aria-hidden="true" />
          )}
          <span className="sr-only">{readOnly ? "Agent, nur lesen" : "Agent"}</span>
        </span>
        {commands.map((command) => (
          <AgentCommandButton
            key={command}
            controller={controller}
            command={command}
            onBlocked={setHint}
          />
        ))}
      </div>
      <p
        role="status"
        data-testid={CHAT_TEST_IDS.agentControlHint}
        className={cn(
          "flex min-w-0 items-start gap-1 text-xs break-words",
          controller.error ? "text-destructive" : "text-muted-foreground",
          message === null && "sr-only",
        )}
      >
        {message !== null ? (
          <>
            <CircleAlertIcon className="mt-px size-4 shrink-0" aria-hidden="true" />
            <span className="min-w-0">{message}</span>
          </>
        ) : null}
      </p>
      <StopConfirmDialog controller={controller} />
    </div>
  );
}
