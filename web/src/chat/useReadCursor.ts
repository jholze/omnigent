/**
 * Channel read cursor sync (FR-007, FR-009).
 *
 * When the newest stored top-level message of the open channel is on screen
 * and the tab is visible, post it as the read cursor. Posts are throttled
 * and only ever move forward: a cursor at or before the last posted one is
 * not sent again. The server keeps the newer cursor anyway.
 *
 * After a successful post the cached bootstrap drops this channel's badge at
 * once when the posted message is still the newest one. The `read.updated`
 * frame then refetches bootstrap for the server counts (see `useChatFrames`).
 */
import { useEffect, useRef, type RefObject } from "react";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";

import {
  ChatApiError,
  postReadCursor,
  teamChatQueryKeys,
  type BootstrapResponse,
  type ChatMessage,
  type ReadCursor,
} from "./chatApi";
import { CHAT_TEST_IDS } from "./testids";

/** At most one read POST per channel in this window. */
export const READ_CURSOR_THROTTLE_MS = 1000;

/** True when `a` is strictly after `b` in message order (created_at, then id). */
export function isCursorAfter(a: ReadCursor, b: ReadCursor | null): boolean {
  if (b === null) return true;
  if (a.created_at !== b.created_at) return a.created_at > b.created_at;
  return a.id > b.id;
}

/** Newest stored top-level message of a page, or null. */
export function newestTopLevel(items: readonly ChatMessage[] | undefined): ReadCursor | null {
  let newest: ReadCursor | null = null;
  for (const item of items ?? []) {
    if (item.thread_root_id !== null || item.id === "") continue;
    const cursor = { id: item.id, created_at: item.created_at };
    if (isCursorAfter(cursor, newest)) newest = cursor;
  }
  return newest;
}

function clearBadge(queryClient: QueryClient, channelId: string): void {
  queryClient.setQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap, (data) => {
    if (data === undefined) return data;
    const clear = (rows: BootstrapResponse["channels"]) =>
      rows.map((row) =>
        row.id === channelId && (row.unread_count !== 0 || row.mention_count !== 0)
          ? { ...row, unread_count: 0, mention_count: 0 }
          : row,
      );
    return { ...data, channels: clear(data.channels), dms: clear(data.dms) };
  });
}

function messageElement(container: HTMLElement, messageId: string): Element | null {
  const rows = container.querySelectorAll(`[data-testid="${CHAT_TEST_IDS.message}"]`);
  for (const row of rows) {
    if (row.getAttribute("data-message-id") === messageId) return row;
  }
  return null;
}

/**
 * Watch the newest message row inside `containerRef` and post the cursor.
 * No-op without `IntersectionObserver` (jsdom, old browsers).
 */
export function useReadCursor(
  channelId: string,
  newest: ReadCursor | null,
  containerRef: RefObject<HTMLElement | null>,
): void {
  const queryClient = useQueryClient();
  const postedRef = useRef<ReadCursor | null>(null);
  const inFlightRef = useRef(false);
  const lastPostAtRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visibleRef = useRef(false);
  const newestRef = useRef(newest);
  newestRef.current = newest;

  useEffect(() => {
    postedRef.current = null;
    lastPostAtRef.current = 0;
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = null;
    };
  }, [channelId]);

  const newestId = newest?.id ?? null;
  const newestAt = newest?.created_at ?? null;

  useEffect(() => {
    const container = containerRef.current;
    if (newestId === null || newestAt === null || container === null) return;
    if (typeof IntersectionObserver === "undefined") return;
    const target: ReadCursor = { id: newestId, created_at: newestAt };

    const flush = (): void => {
      timerRef.current = null;
      if (!visibleRef.current || document.visibilityState !== "visible") return;
      if (!isCursorAfter(target, postedRef.current)) return;
      if (inFlightRef.current) {
        // A newer message arrived during a post: try again after the window.
        timerRef.current = setTimeout(flush, READ_CURSOR_THROTTLE_MS);
        return;
      }
      const wait = lastPostAtRef.current + READ_CURSOR_THROTTLE_MS - Date.now();
      if (wait > 0) {
        timerRef.current = setTimeout(flush, wait);
        return;
      }
      inFlightRef.current = true;
      lastPostAtRef.current = Date.now();
      postReadCursor(channelId, target)
        .then(() => {
          if (isCursorAfter(target, postedRef.current)) postedRef.current = target;
          const current = newestRef.current;
          if (current !== null && current.id === target.id) clearBadge(queryClient, channelId);
        })
        .catch((error: unknown) => {
          const status = error instanceof ChatApiError ? String(error.status) : "error";
          console.warn(`[team-chat] read cursor update failed (${status})`);
        })
        .finally(() => {
          inFlightRef.current = false;
        });
    };
    const schedule = (): void => {
      if (timerRef.current !== null) return;
      timerRef.current = setTimeout(flush, 0);
    };

    // The row renders with the page; wait one frame if it is not there yet.
    let observer: IntersectionObserver | null = null;
    let raf = 0;
    const attach = (): void => {
      const row = messageElement(container, target.id);
      if (row === null) return;
      observer = new IntersectionObserver(
        (entries) => {
          const entry = entries.at(-1);
          visibleRef.current = entry?.isIntersecting ?? false;
          if (visibleRef.current) schedule();
        },
        { threshold: 0 },
      );
      observer.observe(row);
    };
    attach();
    if (observer === null) raf = requestAnimationFrame(attach);

    const onVisibility = (): void => {
      if (document.visibilityState === "visible" && visibleRef.current) schedule();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = null;
      cancelAnimationFrame(raf);
      observer?.disconnect();
      visibleRef.current = false;
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [channelId, newestId, newestAt, containerRef, queryClient]);
}
