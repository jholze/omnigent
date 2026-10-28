/**
 * Smoothed live agent text (FR-080, AC-080a/b, NFR-006).
 *
 * While a turn streams, token deltas arrive faster than the screen paints.
 * This hook coalesces them to at most one React update per animation frame
 * and paints everything received so far on that frame (buffered release, no
 * per-character drip). With reduced motion, when the stream stops, or when
 * the target shrinks or is replaced (turn reset), it returns the target as is.
 */
import { useEffect, useRef, useState } from "react";

import { useChatReducedMotion } from "../MotionRoot";

export function useSmoothedStream(target: string, active: boolean): string {
  const reduced = useChatReducedMotion();
  const immediate = !active || reduced;
  const [shown, setShown] = useState(target);
  const targetRef = useRef(target);
  const frameRef = useRef<number | null>(null);

  // Snap instead of buffering: stream off, reduced motion, or the new target
  // no longer extends what is on screen (shrink / turn reset).
  const snap = immediate || !target.startsWith(shown);
  if (snap && shown !== target) setShown(target);

  useEffect(() => {
    targetRef.current = target;
    if (immediate || frameRef.current !== null) return;
    // One frame per burst: later targets in the same frame reuse it and are
    // read from the ref when it fires.
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      setShown(targetRef.current);
    });
  }, [target, immediate]);

  // Cancel only on unmount, so a burst of target changes still paints once.
  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    },
    [],
  );

  return snap ? target : shown;
}
