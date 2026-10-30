/**
 * Collapsed agent steps in an open Agent-Thread (FR-048, FR-089, FR-090).
 *
 * One row "{n} Schritte"; click or Enter (native button) expands the ordered
 * step list and collapses it again. Expanding is not animated: it is a
 * frequent, keyboard-reachable action.
 *
 * Steps come from the thread's own session, never from the active chat
 * store: that session's todos when it has any (the agent's plan), otherwise
 * the tool calls of its current turn (`tool`, `native_tool`,
 * `terminal_command`). [ANNAHME] Todos win over tool calls so "Schritt n von
 * m" counts the plan the agent works through (AC-090a), not every tool call
 * it makes along the way.
 *
 * An sr-only `role="status"` region (polite) says "{Label}, Schritt {n} von
 * {m}", debounced 500 ms, and "Lauf abgeschlossen" / "Lauf fehlgeschlagen
 * bei {Label}" at the end. It is there whether the list is open or not.
 */
import { useEffect, useId, useRef, useState } from "react";
import { ChevronRightIcon } from "lucide-react";

import { formatToolTitle } from "@/lib/toolTitle";
import type { Bubble, RenderItem } from "@/lib/renderItems";
import { cn } from "@/lib/utils";
import {
  STEP_TEST_IDS,
  TaskSteps,
  useTaskSteps,
  type StepInput,
  type StepStatus,
} from "./TaskSteps";

/** Debounce for the sr-only progress text (FR-090). */
export const ANNOUNCE_DEBOUNCE_MS = 500;

export interface SessionTodo {
  content: string;
  status: "pending" | "in_progress" | "completed";
  activeForm: string;
}

type ToolLike = Extract<RenderItem, { kind: "tool" | "native_tool" | "terminal_command" }>;

function isToolLike(item: RenderItem): item is ToolLike {
  return item.kind === "tool" || item.kind === "native_tool" || item.kind === "terminal_command";
}

function toolLabel(item: Extract<RenderItem, { kind: "tool" }>): string {
  const { name, arguments: args, argsSummary } = item.execution;
  const title = formatToolTitle(name, args, argsSummary);
  const text = [title.verb, title.body].filter((part) => part && part.trim() !== "").join(" ");
  return text.trim() === "" ? name : text;
}

function toolStatus(item: Extract<RenderItem, { kind: "tool" }>): StepStatus {
  switch (item.state) {
    case "output-available":
    case "no-output":
      return "done";
    case "output-error":
    case "cancelled":
      return "error";
    default:
      return "active";
  }
}

/**
 * Tool calls of the current turn: assistant output after the last user
 * input, in order. A terminal command is one step from its input to its
 * output; it is active until the output arrives while the turn runs.
 */
export function currentTurnToolSteps(bubbles: readonly Bubble[], working: boolean): StepInput[] {
  let lastUser = -1;
  bubbles.forEach((bubble, index) => {
    if (bubble.kind === "user") lastUser = index;
  });
  const items: ToolLike[] = [];
  bubbles.forEach((bubble, index) => {
    if (index < lastUser || bubble.kind !== "assistant") return;
    items.push(...bubble.items.filter(isToolLike));
  });
  const steps: StepInput[] = [];
  items.forEach((item, index) => {
    const id = `${item.kind}:${item.itemId ?? index}`;
    if (item.kind === "tool") {
      steps.push({
        id: `tool:${item.execution.callId || item.itemId || index}`,
        label: toolLabel(item),
        status: toolStatus(item),
        durationS: item.duration ?? null,
      });
      return;
    }
    if (item.kind === "native_tool") {
      // Provider-managed tools arrive finished.
      steps.push({ id, label: item.label || item.toolType, status: "done" });
      return;
    }
    if (item.terminalKind !== "input") return;
    const next = items.slice(index + 1).find((later) => later.kind === "terminal_command");
    const answered =
      next !== undefined && next.kind === "terminal_command" && next.terminalKind === "output";
    const status: StepStatus = answered || !working ? "done" : "active";
    steps.push({ id, label: (item.input ?? "").trim() || "Terminal", status });
  });
  return steps;
}

/** Key for one todo; the content names it, the index separates duplicates. */
function todoKey(todo: SessionTodo, index: number, todos: readonly SessionTodo[]): string {
  const dupes = todos.slice(0, index).filter((other) => other.content === todo.content).length;
  return dupes === 0 ? todo.content : `${todo.content}#${dupes}`;
}

interface TodoTiming {
  start: number;
  end?: number;
}

/**
 * Seconds per completed todo, measured in this tab from the moment it was
 * first seen in progress to the moment it was first seen completed. Todos
 * that were already done when the thread opened have no duration (the
 * session reports none).
 */
function useTodoDurations(todos: readonly SessionTodo[]): Record<string, number> {
  const timings = useRef<Record<string, TodoTiming>>({});
  const [durations, setDurations] = useState<Record<string, number>>({});
  useEffect(() => {
    const now = Date.now();
    const next: Record<string, number> = {};
    let changed = false;
    todos.forEach((todo, index) => {
      const key = todoKey(todo, index, todos);
      const timing = timings.current[key];
      if (todo.status === "in_progress" && timing === undefined) {
        timings.current[key] = { start: now };
      } else if (todo.status === "completed" && timing !== undefined) {
        if (timing.end === undefined) {
          timing.end = now;
          changed = true;
        }
        next[key] = (timing.end - timing.start) / 1000;
      }
    });
    if (changed) setDurations(next);
  }, [todos]);
  return durations;
}

export function todoSteps(
  todos: readonly SessionTodo[],
  durations: Record<string, number>,
): StepInput[] {
  return todos.map((todo, index) => {
    const key = todoKey(todo, index, todos);
    const status: StepStatus =
      todo.status === "completed" ? "done" : todo.status === "in_progress" ? "active" : "pending";
    return { id: `todo:${key}`, label: todo.content, status, durationS: durations[key] ?? null };
  });
}

/** Text of the collapsed row: "1 Schritt", "3 Schritte". */
export function stepsSummaryText(count: number): string {
  return `${count} ${count === 1 ? "Schritt" : "Schritte"}`;
}

/** Text in the status region, settled for 500 ms before it is announced. */
function useDebounced(value: string | null, delayMs: number): string | null {
  const [shown, setShown] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setShown(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return shown;
}

/** False while `ref` is scrolled out of view; loops pause then. */
function useOnScreen(ref: React.RefObject<HTMLElement | null>): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const element = ref.current;
    if (element === null || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry !== undefined) setOnScreen(entry.isIntersecting);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return onScreen;
}

export interface StepsGroupProps {
  /** Bubbles of the thread's session (`buildBubbles`). */
  bubbles: readonly Bubble[];
  /** That session's todos (`ConversationState.todos`). */
  todos: readonly SessionTodo[];
  /** The session's turn is running. */
  working: boolean;
  className?: string;
}

export function StepsGroup({ bubbles, todos, working, className }: StepsGroupProps) {
  const durations = useTodoDurations(todos);
  const fromTodos = todos.length > 0;
  const input = fromTodos ? todoSteps(todos, durations) : currentTurnToolSteps(bubbles, working);
  const settled = fromTodos ? todos.every((todo) => todo.status === "completed") : !working;
  const state = useTaskSteps(input, settled);
  const announced = useDebounced(state.announcement, ANNOUNCE_DEBOUNCE_MS);
  const [open, setOpen] = useState(false);
  const listId = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const onScreen = useOnScreen(rootRef);

  if (state.steps.length === 0) return null;

  return (
    <div ref={rootRef} data-testid="steps-group" className={cn("flex min-w-0 flex-col", className)}>
      <button
        type="button"
        data-testid={STEP_TEST_IDS.summary}
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "-mx-1 flex min-h-11 w-fit max-w-full min-w-0 items-center gap-1 rounded-sm px-1 text-left text-xs font-medium text-muted-foreground md:min-h-7",
          "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        )}
      >
        <ChevronRightIcon
          className={cn("size-4 shrink-0", open && "rotate-90")}
          aria-hidden="true"
        />
        <span className="tabular-nums">{stepsSummaryText(state.steps.length)}</span>
      </button>
      {open ? (
        <TaskSteps id={listId} steps={state.steps} paused={!onScreen} className="pt-1 pl-1" />
      ) : null}
      <span role="status" aria-live="polite" className="sr-only">
        {announced ?? ""}
      </span>
    </div>
  );
}
