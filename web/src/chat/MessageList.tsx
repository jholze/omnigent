/**
 * Scrollable message list for a channel or a thread.
 *
 * Stays anchored at the bottom only while the reader is at the bottom, or
 * when the reader sends (FR-080). When streamed agent output grows while the
 * reader has scrolled up, the position stays put and a "Neue Nachrichten"
 * pill offers the jump (AC-080a). New messages
 * from others are announced politely, at most once per 2 s (NFR-009).
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { DirectoryEntry } from "./chatApi";
import { MessageItem, principalLabel, type MessageView } from "./MessageItem";
import { NewMessagesPill } from "./agent/NewMessagesPill";
import { CHAT_TEST_IDS } from "./testids";

const GROUP_WINDOW_MS = 5 * 60 * 1000;
const BOTTOM_SLACK_PX = 48;
export const ANNOUNCE_INTERVAL_MS = 2000;

function isCompact(previous: MessageView | undefined, message: MessageView): boolean {
  if (previous === undefined) return false;
  if (previous.authorId !== message.authorId) return false;
  if (previous.authorKind === "system" || message.authorKind === "system") return false;
  return message.createdAtMs - previous.createdAtMs < GROUP_WINDOW_MS;
}

/** More rows than this arriving at once is a backfill, not a live arrival. */
export const LIVE_BATCH_MAX = 3;

/**
 * Decide once per row whether it plays the enter animation (FR-078): only
 * rows that arrive after the first paint, at the end of the list, from
 * someone else, in a small batch. The first page, scroll-back pages,
 * backfill bursts and the reader's own sends (NFR-006) stay static.
 *
 * The decision is stored per key, so a re-render (or React's double render
 * in dev) never flips it.
 */
export function useEnterKeys(messages: MessageView[], me: string): Set<string> {
  const decidedRef = useRef<Map<string, boolean> | null>(null);
  const newestRef = useRef(0);
  let decided = decidedRef.current;
  if (decided === null) {
    // The first page is history, not news.
    decided = new Map(messages.map((message) => [message.key, false]));
    decidedRef.current = decided;
    newestRef.current = messages.reduce((max, message) => Math.max(max, message.createdAtMs), 0);
  }
  const fresh = messages.filter((message) => !decided.has(message.key));
  const burst = fresh.length > LIVE_BATCH_MAX;
  for (const message of fresh) {
    const live =
      !burst &&
      message.authorId !== me &&
      message.status === "sent" &&
      message.createdAtMs >= newestRef.current;
    decided.set(message.key, live);
  }
  for (const message of fresh) {
    newestRef.current = Math.max(newestRef.current, message.createdAtMs);
  }
  const keys = new Set<string>();
  for (const message of messages) if (decided.get(message.key)) keys.add(message.key);
  return keys;
}

/** Batch "Neue Nachricht von Anna" announcements to one per interval. */
function useNewMessageAnnouncement(
  messages: MessageView[],
  me: string,
  directory: Map<string, DirectoryEntry>,
): string {
  const [announcement, setAnnouncement] = useState("");
  const seenRef = useRef<Set<string> | null>(null);
  const pendingRef = useRef<string[]>([]);
  const lastRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const ids = messages.flatMap((message) => (message.id ? [message.id] : []));
    if (seenRef.current === null) {
      // The first page is history, not news.
      seenRef.current = new Set(ids);
      return;
    }
    const seen = seenRef.current;
    for (const message of messages) {
      if (message.id === null || seen.has(message.id)) continue;
      seen.add(message.id);
      if (message.authorId !== me) pendingRef.current.push(message.authorId);
    }
    if (pendingRef.current.length === 0 || timerRef.current !== null) return;
    const flush = () => {
      timerRef.current = null;
      const authors = pendingRef.current.splice(0);
      if (authors.length === 0) return;
      lastRef.current = Date.now();
      setAnnouncement(
        authors.length === 1
          ? `Neue Nachricht von ${principalLabel(authors[0], directory)}`
          : `${authors.length} neue Nachrichten`,
      );
    };
    const wait = Math.max(0, lastRef.current + ANNOUNCE_INTERVAL_MS - Date.now());
    timerRef.current = setTimeout(flush, wait);
  }, [messages, me, directory]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    },
    [],
  );

  return announcement;
}

export interface MessageListProps {
  messages: MessageView[];
  directory: Map<string, DirectoryEntry>;
  me: string;
  /** Accessible name of the list, e.g. "Nachrichten in #general". */
  label: string;
  /** Rendered above the rows inside the scroller (thread root). */
  header?: ReactNode;
  /** Shown instead of rows when there are none. */
  empty?: ReactNode;
  threadHrefFor?: (message: MessageView) => string | undefined;
  replyCounts?: Map<string, number>;
  onRetry: (clientId: string) => void;
  /** Extra content next to a thread root's reply count (agent status chip). */
  renderThreadAccessory?: (message: MessageView) => ReactNode;
  /** Rendered below the rows inside the scroller (live agent output). */
  footer?: ReactNode;
}

export function MessageList({
  messages,
  directory,
  me,
  label,
  header,
  empty,
  threadHrefFor,
  replyCounts,
  onRetry,
  renderThreadAccessory,
  footer,
}: MessageListProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const footerRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const [pill, setPill] = useState(false);
  const announcement = useNewMessageAnnouncement(messages, me, directory);
  const enterKeys = useEnterKeys(messages, me);
  const last = messages.at(-1);
  const lastKey = last?.key;
  const ownSend = last !== undefined && last.authorId === me && last.status !== "sent";

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (scroller === null) return;
    if (atBottomRef.current || ownSend) {
      scroller.scrollTop = scroller.scrollHeight;
      atBottomRef.current = true;
    }
  }, [lastKey, messages.length, ownSend]);

  // Streamed agent output grows the footer without adding a row. Follow it
  // only while the reader is at the bottom (FR-080, AC-080b); otherwise keep
  // the position and offer the pill (AC-080a).
  useEffect(() => {
    const scroller = scrollerRef.current;
    const content = contentRef.current;
    const footerBox = footerRef.current;
    if (scroller === null || content === null || typeof ResizeObserver === "undefined") return;
    let footerHeight = footerBox?.offsetHeight ?? 0;
    const observer = new ResizeObserver(() => {
      const grown = footerBox !== null && footerBox.offsetHeight > footerHeight;
      if (footerBox !== null) footerHeight = footerBox.offsetHeight;
      if (atBottomRef.current) scroller.scrollTop = scroller.scrollHeight;
      else if (grown) setPill(true);
    });
    observer.observe(content);
    if (footerBox !== null) observer.observe(footerBox);
    return () => observer.disconnect();
  }, []);

  const jumpToBottom = () => {
    const scroller = scrollerRef.current;
    if (scroller !== null) scroller.scrollTop = scroller.scrollHeight;
    atBottomRef.current = true;
    setPill(false);
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollerRef}
        data-testid={CHAT_TEST_IDS.messageList}
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
        onScroll={(event) => {
          const el = event.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_SLACK_PX;
          atBottomRef.current = atBottom;
          if (atBottom) setPill(false);
        }}
      >
        <div ref={contentRef} className={cn(messages.length === 0 && !footer && "h-full")}>
          {header}
          {messages.length === 0 ? (
            empty
          ) : (
            <ol aria-label={label} className={cn("flex flex-col", footer ? "pb-1" : "pb-3")}>
              {messages.map((message, index) => (
                <MessageItem
                  key={message.key}
                  message={message}
                  directory={directory}
                  compact={isCompact(messages[index - 1], message)}
                  threadHref={threadHrefFor?.(message)}
                  replyCount={message.id ? (replyCounts?.get(message.id) ?? 0) : 0}
                  onRetry={onRetry}
                  threadAccessory={renderThreadAccessory?.(message)}
                  enter={enterKeys.has(message.key)}
                />
              ))}
            </ol>
          )}
          <div ref={footerRef}>{footer}</div>
        </div>
        <div aria-live="polite" aria-atomic="true" className="sr-only">
          {announcement}
        </div>
      </div>
      {pill ? <NewMessagesPill onClick={jumpToBottom} /> : null}
    </div>
  );
}
