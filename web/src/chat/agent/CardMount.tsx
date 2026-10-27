/**
 * Wrapper for an approval card in the agent thread (FR-078, FR-079).
 *
 * `animate`: the card arrived live, so it springs in with `cardMount`
 * (500/40 from scale 0.97, settles in under 300 ms). Reduced motion swaps
 * that for a 120 ms opacity fade. Cards that were already there mount static.
 */
import type { ReactNode } from "react";
import { m } from "motion/react";

import { cardMount } from "../motion";
import { useChatReducedMotion } from "../MotionRoot";

export interface CardMountProps {
  animate: boolean;
  className?: string;
  children: ReactNode;
}

export function CardMount({ animate, className, children }: CardMountProps) {
  const reduced = useChatReducedMotion();
  if (!animate) {
    return <div className={className}>{children}</div>;
  }
  const preset = cardMount(reduced);
  return (
    <m.div
      initial={preset.initial}
      animate={preset.animate}
      transition={preset.transition}
      data-motion={preset.name}
      data-motion-duration={preset.durationMs}
      className={className}
    >
      {children}
    </m.div>
  );
}
