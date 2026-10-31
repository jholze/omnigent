/**
 * Unread count badge for the chat sidebar (FR-078 badge pulse, FR-079).
 *
 * When `count` changes after the first render, the badge pulses
 * 1 → 1.08 → 1 over 150 ms (`badgePulse`). Reduced motion: no pulse. The
 * first value and a count of 0 (badge hidden) are static. `ChatSidebar`
 * renders its unread and mention pills through this component; there the
 * link carries the accessible name, so the badge is decorative (no `label`).
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
  /**
   * Accessible text, e.g. "3 ungelesene Nachrichten". Omit it when the
   * surrounding control already names the count: the badge is then
   * `aria-hidden` so screen readers do not hear the count twice.
   */
  label?: string;
  /** Text before the count, e.g. "@" for mentions. */
  prefix?: string;
  testId?: string;
  /** Mentions use the accent tone; plain unread stays muted. */
  tone?: "muted" | "accent";
  className?: string;
}

export function CountBadge({
  count,
  label,
  prefix,
  testId,
  tone = "muted",
  className,
}: CountBadgeProps) {
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
      aria-hidden={label === undefined ? "true" : undefined}
      title={label}
      data-testid={testId}
      data-motion={pulse === null ? "badge-pulse-reduced" : "badge-pulse"}
      data-motion-duration={pulse === null ? 0 : durations.micro}
      className={cn(
        "inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full px-1 text-[11px] leading-none font-medium tabular-nums",
        tone === "accent" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      {prefix}
      {countBadgeText(count)}
    </span>
  );
}
