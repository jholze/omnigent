/**
 * Unread count badge for the chat sidebar (FR-078 badge pulse, FR-079).
 *
 * When `count` changes after the first render, the badge pulses
 * 1 → 1.08 → 1 over 150 ms (`badgePulse`). Reduced motion: no pulse. The
 * first value and a count of 0 (badge hidden) are static. T-111 consumes
 * this component for the sidebar unread counts.
 */
import { useEffect, useRef } from "react";
import { useAnimate } from "motion/react";

import { cn } from "@/lib/utils";
import { badgePulse, durations } from "./motion";
import { useChatReducedMotion } from "./MotionRoot";

/** Counts above this read "99+". */
export const COUNT_BADGE_MAX = 99;

export function countBadgeText(count: number): string {
  return count > COUNT_BADGE_MAX ? `${COUNT_BADGE_MAX}+` : String(count);
}

export interface CountBadgeProps {
  count: number;
  /** Accessible text, e.g. "3 ungelesene Nachrichten". */
  label: string;
  /** Mentions use the accent tone; plain unread stays muted. */
  tone?: "muted" | "accent";
  className?: string;
}

export function CountBadge({ count, label, tone = "muted", className }: CountBadgeProps) {
  const [scope, animate] = useAnimate<HTMLSpanElement>();
  const reduced = useChatReducedMotion();
  const previousRef = useRef(count);

  useEffect(() => {
    const previous = previousRef.current;
    previousRef.current = count;
    if (previous === count || scope.current === null) return;
    const pulse = badgePulse(reduced);
    if (pulse === null) return;
    void animate(scope.current, pulse.keyframes, pulse.transition);
  }, [count, reduced, animate, scope]);

  if (count <= 0) return null;
  const pulse = badgePulse(reduced);
  return (
    <span
      ref={scope}
      aria-label={label}
      title={label}
      data-motion={pulse === null ? "badge-pulse-reduced" : "badge-pulse"}
      data-motion-duration={pulse === null ? 0 : durations.micro}
      className={cn(
        "inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full px-1 text-[11px] leading-none font-medium tabular-nums",
        tone === "accent" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      {countBadgeText(count)}
    </span>
  );
}
