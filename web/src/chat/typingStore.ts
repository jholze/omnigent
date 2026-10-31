/**
 * Who is typing in which channel (FR-032, AC-032a).
 *
 * Fed by body-less `typing` frames. A person stays listed until
 * `TYPING_HIDE_MS` after their last `active: true` frame, or leaves at once on
 * `active: false`. Nothing is persisted; a reload starts empty.
 */
import { create } from "zustand";

/** The server sends at most one active frame per 3 s; hide after 4 s of silence. */
export const TYPING_HIDE_MS = 4000;

/** A body-less typing frame. Anything else a sender attached is dropped. */
export interface ChatTypingFrame {
  type: "chat";
  kind: "typing";
  channel_id: string;
  principal_id: string;
  active: boolean;
}

interface TypingState {
  /** channel id -> principal ids in the order they started typing. */
  byChannel: Record<string, string[]>;
  apply: (frame: ChatTypingFrame) => void;
}

const timers = new Map<string, ReturnType<typeof setTimeout>>();

function timerKey(channelId: string, principalId: string): string {
  return `${channelId}\u0000${principalId}`;
}

function clearTimer(key: string): void {
  const timer = timers.get(key);
  if (timer !== undefined) clearTimeout(timer);
  timers.delete(key);
}

function without(
  byChannel: Record<string, string[]>,
  channelId: string,
  principalId: string,
): Record<string, string[]> {
  const current = byChannel[channelId];
  if (!current?.includes(principalId)) return byChannel;
  const rest = current.filter((id) => id !== principalId);
  if (rest.length > 0) return { ...byChannel, [channelId]: rest };
  return Object.fromEntries(Object.entries(byChannel).filter(([id]) => id !== channelId));
}

export const useTypingStore = create<TypingState>((set) => ({
  byChannel: {},
  apply: (frame) => {
    const { channel_id: channelId, principal_id: principalId } = frame;
    const key = timerKey(channelId, principalId);
    clearTimer(key);
    if (!frame.active) {
      set((state) => ({ byChannel: without(state.byChannel, channelId, principalId) }));
      return;
    }
    timers.set(
      key,
      setTimeout(() => {
        timers.delete(key);
        set((state) => ({ byChannel: without(state.byChannel, channelId, principalId) }));
      }, TYPING_HIDE_MS),
    );
    set((state) => {
      const current = state.byChannel[channelId] ?? [];
      if (current.includes(principalId)) return state;
      return { byChannel: { ...state.byChannel, [channelId]: [...current, principalId] } };
    });
  },
}));

/** Apply one typing frame. Exported for the frame router. */
export function applyTypingFrame(frame: ChatTypingFrame): void {
  useTypingStore.getState().apply(frame);
}

/** Drop every typer and timer (tests, sign-out). */
export function resetTypingStore(): void {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
  useTypingStore.setState({ byChannel: {} });
}

const NO_TYPERS: readonly string[] = [];

/** Principal ids typing in *channelId*, without *me*. Stable when empty. */
export function useChannelTypers(channelId: string, me: string | undefined): readonly string[] {
  const typers = useTypingStore((state) => state.byChannel[channelId] ?? NO_TYPERS);
  if (me === undefined || !typers.includes(me)) return typers;
  const others = typers.filter((id) => id !== me);
  return others.length === 0 ? NO_TYPERS : others;
}

/**
 * The visible line: one name, two names, or "Mehrere Personen schreiben…".
 * `null` when nobody else is typing.
 */
export function typingLine(names: readonly string[]): string | null {
  if (names.length === 0) return null;
  if (names.length === 1) return `${names[0]} schreibt…`;
  if (names.length === 2) return `${names[0]} und ${names[1]} schreiben…`;
  return "Mehrere Personen schreiben…";
}
