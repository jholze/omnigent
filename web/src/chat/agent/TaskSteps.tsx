/**
 * Agent step list (FR-089, FR-090; ADR-0008).
 *
 * Behaviour rebuilt after the interior.dev TaskSteps pattern without copying
 * its code (Q-20 open): pending = dot, active = spinning arc plus shimmer on
 * the label, done = check that pops in plus the duration as mono meta on the
 * right, error = red cross that pops in. Once a step failed, every later
 * step is shown as pending without a duration.
 *
 * Motion comes only from `../motion` (`pop`, `cell`, `spinSeconds`,
 * `shimmerSeconds`, `REDUCED_FADE_MS`). Under reduced motion the icon swap
 * is an opacity fade of at most 120 ms: no scale, no rotation, no shimmer.
 * The first status a row mounts with is static; only a change animates.
 *
 * The arc and shimmer loop only while the list is on screen (`paused`).
 */
import { useState } from "react";
import { m } from "motion/react";
import { CheckIcon, LoaderCircleIcon, XIcon } from "lucide-react";

import { Shimmer } from "@/components/ai-elements/shimmer";
import { formatToolDuration } from "@/components/blocks/ToolCard";
import { cn } from "@/lib/utils";
import {
  cell,
  colorTransitionStyle,
  easeOut,
  pop,
  REDUCED_FADE_MS,
  shimmerSeconds,
  spinSeconds,
} from "../motion";
import { useChatReducedMotion } from "../MotionRoot";

/** Stable `data-testid` values (Playwright and Vitest read them). */
export const STEP_TEST_IDS = {
  summary: "steps-summary",
  list: "steps-list",
  row: "step-row",
  status: "step-status",
} as const;

export type StepStatus = "pending" | "active" | "done" | "error";

export interface StepInput {
  /** Stable per step (tool call id, todo key). */
  id: string;
  label: string;
  status: StepStatus;
  /** Seconds the step took; shown only when `done`. */
  durationS?: number | null;
}

export interface Step {
  id: string;
  label: string;
  status: StepStatus;
  durationS: number | null;
}

/**
 * Apply the error rule (FR-089): after the first error every later step is
 * `pending` with no duration, whatever its source reported.
 */
export function resolveSteps(steps: readonly StepInput[]): Step[] {
  let failed = false;
  return steps.map((step) => {
    if (failed) return { id: step.id, label: step.label, status: "pending", durationS: null };
    if (step.status === "error") failed = true;
    const durationS =
      step.status === "done" && typeof step.durationS === "number" && step.durationS >= 0
        ? step.durationS
        : null;
    return { id: step.id, label: step.label, status: step.status, durationS };
  });
}

/** "3.4s"; under a second "420ms" (same format as the tool card). */
export function formatStepDuration(seconds: number): string {
  return formatToolDuration(seconds);
}

export interface TaskStepsState {
  steps: Step[];
  /** Index of the active step, else of the failed step, else -1. */
  currentIndex: number;
  failedStep: Step | null;
  /** Every step done and the run settled. */
  complete: boolean;
  /** Text for the sr-only status region; `null` says nothing. */
  announcement: string | null;
}

/** "{Label}, Schritt {n} von {m}" (FR-090). */
export function progressText(label: string, index: number, total: number): string {
  return `${label}, Schritt ${index + 1} von ${total}`;
}

export const RUN_DONE_TEXT = "Lauf abgeschlossen";
export const runFailedText = (label: string) => `Lauf fehlgeschlagen bei ${label}`;

/**
 * Status logic for the step list. `settled`: the run will not add more
 * steps (the turn ended, or every todo is completed), so an all-done list
 * counts as complete.
 */
export function useTaskSteps(input: readonly StepInput[], settled: boolean): TaskStepsState {
  return taskStepsState(resolveSteps(input), settled);
}

export function taskStepsState(steps: Step[], settled: boolean): TaskStepsState {
  const total = steps.length;
  const failedIndex = steps.findIndex((step) => step.status === "error");
  const activeIndex = steps.findIndex((step) => step.status === "active");
  const failedStep = failedIndex >= 0 ? steps[failedIndex] : null;
  const complete = total > 0 && settled && steps.every((step) => step.status === "done");
  let announcement: string | null = null;
  if (failedStep !== null) announcement = runFailedText(failedStep.label);
  else if (complete) announcement = RUN_DONE_TEXT;
  else if (activeIndex >= 0)
    announcement = progressText(steps[activeIndex].label, activeIndex, total);
  return {
    steps,
    currentIndex: failedIndex >= 0 ? failedIndex : activeIndex,
    failedStep,
    complete,
    announcement,
  };
}

/** Status glyph. Animates only when the status changes after mount. */
function StepIcon({ status, paused }: { status: StepStatus; paused: boolean }) {
  const reduced = useChatReducedMotion();
  const [first] = useState(status);
  const [changed, setChanged] = useState(false);
  if (!changed && status !== first) setChanged(true);

  let glyph;
  if (status === "done") {
    glyph = (
      <CheckIcon className="size-3.5 text-foreground" strokeWidth={2.25} aria-hidden="true" />
    );
  } else if (status === "error") {
    glyph = <XIcon className="size-3.5 text-destructive" strokeWidth={2.25} aria-hidden="true" />;
  } else if (status === "active") {
    const spinning = !reduced && !paused;
    glyph = (
      <LoaderCircleIcon
        className={cn("size-3.5 text-foreground", spinning && "animate-spin")}
        style={spinning ? { animationDuration: `${spinSeconds}s` } : undefined}
        data-spinning={spinning || undefined}
        aria-hidden="true"
      />
    );
  } else {
    glyph = <span className="size-1.5 rounded-full bg-muted-foreground/60" aria-hidden="true" />;
  }

  // Check and cross pop from 0.4 (spec exception for this tiny icon); the
  // arc settles in with the cell spring; the dot only fades.
  let motionProps: Record<string, unknown> = { initial: false };
  if (changed) {
    if (reduced) {
      motionProps = {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        transition: { duration: REDUCED_FADE_MS / 1000, ease: easeOut },
        "data-motion": "step-icon-reduced",
        "data-motion-duration": REDUCED_FADE_MS,
      };
    } else if (status === "done" || status === "error") {
      motionProps = {
        initial: { opacity: 0, transform: "scale(0.4)" },
        animate: { opacity: 1, transform: "scale(1)" },
        transition: pop,
        "data-motion": "step-icon-pop",
      };
    } else if (status === "active") {
      motionProps = {
        initial: { opacity: 0, transform: "scale(0.95)" },
        animate: { opacity: 1, transform: "scale(1)" },
        transition: cell,
        "data-motion": "step-icon-cell",
      };
    } else {
      motionProps = {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        transition: cell,
        "data-motion": "step-icon-cell",
      };
    }
  }

  return (
    <span
      data-testid={STEP_TEST_IDS.status}
      data-status={status}
      className="flex size-5 shrink-0 items-center justify-center"
    >
      <m.span key={status} {...motionProps} className="flex items-center justify-center">
        {glyph}
      </m.span>
    </span>
  );
}

function StepLabel({ step, paused }: { step: Step; paused: boolean }) {
  const reduced = useChatReducedMotion();
  const className = "min-w-0 break-words text-pretty [overflow-wrap:anywhere]";
  if (step.status === "active" && !reduced && !paused) {
    return (
      <Shimmer as="span" duration={shimmerSeconds} className={cn(className, "inline")}>
        {step.label}
      </Shimmer>
    );
  }
  return <span className={className}>{step.label}</span>;
}

export interface TaskStepsProps {
  id?: string;
  steps: Step[];
  /** Stop the arc and shimmer (list off screen). */
  paused?: boolean;
  className?: string;
}

/** Ordered step list; the active row carries `aria-current="step"`. */
export function TaskSteps({ id, steps, paused = false, className }: TaskStepsProps) {
  const reduced = useChatReducedMotion();
  const colour = colorTransitionStyle(reduced);
  return (
    <ol
      id={id}
      data-testid={STEP_TEST_IDS.list}
      aria-label="Schritte des Agenten"
      className={cn("flex min-w-0 flex-col gap-0.5", className)}
    >
      {steps.map((step) => (
        <li
          key={step.id}
          data-testid={STEP_TEST_IDS.row}
          data-status={step.status}
          aria-current={step.status === "active" ? "step" : undefined}
          style={colour}
          className={cn(
            "flex min-w-0 items-start gap-2 py-0.5 text-sm",
            step.status === "pending" ? "text-muted-foreground" : "text-foreground",
            step.status === "error" && "text-destructive",
          )}
        >
          <StepIcon status={step.status} paused={paused} />
          <span className="flex min-w-0 flex-1 pt-px">
            <StepLabel step={step} paused={paused} />
            {step.status === "error" ? <span className="sr-only"> (fehlgeschlagen)</span> : null}
          </span>
          {step.durationS !== null ? (
            <span className="shrink-0 pt-px font-mono text-xs text-muted-foreground tabular-nums">
              {formatStepDuration(step.durationS)}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
