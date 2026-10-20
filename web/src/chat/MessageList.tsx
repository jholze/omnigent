/**
 * Scrollable message list for a channel or a thread.
 *
 * Stays anchored at the bottom only while the reader is at the bottom, or
 * when the reader sends (FR-080, R0: anchor without smoothing). New messages
 * from others are announced politely, at most once per 2 s (NFR-009).
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { DirectoryEntry } from "./chatApi";
import { MessageItem, principalLabel, type MessageView } from "./MessageItem";
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
  const atBottomRef = useRef(true);
  const announcement = useNewMessageAnnouncement(messages, me, directory);
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
  // only while the reader is at the bottom (FR-080); no smoothing.
  useEffect(() => {
    const scroller = scrollerRef.current;
    const content = contentRef.current;
    if (scroller === null || content === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (atBottomRef.current) scroller.scrollTop = scroller.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={scrollerRef}
      data-testid={CHAT_TEST_IDS.messageList}
      className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
      onScroll={(event) => {
        const el = event.currentTarget;
        atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_SLACK_PX;
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
              />
            ))}
          </ol>
        )}
        {footer}
      </div>
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>
    </div>
  );
}
