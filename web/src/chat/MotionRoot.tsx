/**
 * Motion root for the team chat (ADR-0008, FR-078, FR-079).
 *
 * `LazyMotion` with `domAnimation` keeps the bundle small (`m.*` components
 * only; `strict` rejects the full `motion.*` components). `MotionConfig
 * reducedMotion="user"` makes Motion drop transforms when the OS asks for
 * reduced motion; `useChatReducedMotion` lets presets pick their opacity-only
 * variant (≤ 120 ms) as well.
 */
import type { ReactNode } from "react";
import { domAnimation, LazyMotion, MotionConfig, useReducedMotion } from "motion/react";

export function MotionRoot({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}

/** True while the OS reports `prefers-reduced-motion: reduce`. */
export function useChatReducedMotion(): boolean {
  return useReducedMotion() === true;
}
