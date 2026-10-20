/**
 * Close the gap between the thread's session snapshot and its live stream
 * (FR-044, AC-044a).
 *
 * The stream has no replay. A status change published after the snapshot
 * was read and before the stream connected reaches neither, so the chip
 * would stay on the snapshot value until the next change. Once the stream
 * is connected and the snapshot is in, this re-reads the session status
 * once. It only applies the result when no live event landed during that
 * read, so a fresher stream value always wins.
 *
 * The session chat reconciles its active conversation itself; an open
 * Agent-Thread binds a background entry, which that path skips.
 */
import { useEffect, useState } from "react";

import { getSessionSlim } from "@/lib/sessionsApi";
import {
  conversationStreamRevision,
  subscribeConversationStreamConnected,
} from "@/store/chatStore";
import { conversationRegistry } from "@/store/conversationRegistry";

export function useStatusAfterConnect(sessionId: string, snapshotLoading: boolean): void {
  const [connects, setConnects] = useState(0);

  useEffect(
    () =>
      subscribeConversationStreamConnected((id) => {
        if (id === sessionId) setConnects((count) => count + 1);
      }),
    [sessionId],
  );

  useEffect(() => {
    if (connects === 0 || snapshotLoading) return;
    const entry = conversationRegistry.peek(sessionId);
    if (entry === undefined) return;
    const controller = new AbortController();
    const statusBefore = entry.getState().sessionStatus;
    const revisionBefore = conversationStreamRevision(sessionId);
    void getSessionSlim(sessionId, { signal: controller.signal }).then(
      (session) => {
        if (controller.signal.aborted || entry.disposed) return;
        if (conversationRegistry.peek(sessionId) !== entry) return;
        if (conversationStreamRevision(sessionId) !== revisionBefore) return;
        const current = entry.getState();
        if (current.sessionStatus !== statusBefore || current.sessionStatus === session.status) {
          return;
        }
        entry.setState({ sessionStatus: session.status });
      },
      () => {
        // A failed re-read leaves the snapshot value; the next event corrects it.
      },
    );
    return () => controller.abort();
  }, [sessionId, connects, snapshotLoading]);
}
