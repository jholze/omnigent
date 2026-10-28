/**
 * Run-stuck banner (FR-053, AC-053a): the session is running and no stream
 * event arrived for 120 s. Offers the two ways out: interrupt the turn, or
 * open the session to look at it. Static, no motion (shows once, stays).
 */
import { useEffect, useState } from "react";
import { ClockAlertIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { CHAT_TEST_IDS } from "../testids";
import { AgentCommandButton } from "./AgentControls";
import type { AgentControlsController } from "./useAgentControls";
import { OpenAgentSessionLink } from "./sessionAccess";

/** FR-053: quiet time while `running` before the banner shows. */
export const RUN_STUCK_MS = 120_000;

/**
 * True once `running` held for `ms` without `activity` changing. Any new
 * value of `activity` (the live conversation state, which changes on every
 * stream event) restarts the clock.
 */
export function useRunStuck(running: boolean, activity: unknown, ms = RUN_STUCK_MS): boolean {
  const [stuckOn, setStuckOn] = useState<{ activity: unknown } | null>(null);
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => setStuckOn({ activity }), ms);
    return () => window.clearTimeout(timer);
  }, [running, activity, ms]);
  return running && stuckOn !== null && stuckOn.activity === activity;
}

export interface RunStuckBannerProps {
  sessionId: string;
  controller: AgentControlsController;
  className?: string;
}

export function RunStuckBanner({ sessionId, controller, className }: RunStuckBannerProps) {
  const [hint, setHint] = useState<string | null>(null);
  const error = controller.error?.command === "interrupt" ? controller.error.text : null;
  const message = error ?? hint;
  return (
    <div
      role="alert"
      data-testid={CHAT_TEST_IDS.runStuckBanner}
      className={cn(
        "mx-4 mb-3 flex min-w-0 flex-col gap-1 rounded-md border border-border bg-muted/50 py-1.5 pr-1.5 pl-3",
        className,
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2">
        <ClockAlertIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Agent reagiert nicht</p>
          <p className="text-xs text-muted-foreground">Seit 2 Minuten keine Ausgabe.</p>
        </div>
        <div className="flex shrink-0 items-center gap-1 max-sm:w-full max-sm:justify-end">
          <AgentCommandButton
            controller={controller}
            command="interrupt"
            variant="outline"
            testId={CHAT_TEST_IDS.runStuckInterrupt}
            onBlocked={setHint}
          />
          <OpenAgentSessionLink sessionId={sessionId} testId={CHAT_TEST_IDS.runStuckOpenSession} />
        </div>
      </div>
      {message !== null ? (
        <p
          className={cn(
            "pb-1 text-xs break-words",
            error ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
