/**
 * "Anna schreibt…" above a channel composer (FR-032, AC-032a).
 *
 * Plain text, not a control. The row keeps one line of height so the list
 * does not jump when someone starts or stops typing; long names wrap. No
 * motion beyond appearing and disappearing.
 */
import { useMemo } from "react";

import type { DirectoryEntry } from "./chatApi";
import { chatPersonName } from "./NeedsYouView";
import { CHAT_TEST_IDS } from "./testids";
import { typingLine, useChannelTypers } from "./typingStore";

export function TypingIndicator({
  channelId,
  me,
  directory,
}: {
  channelId: string;
  me: string;
  directory: Map<string, DirectoryEntry>;
}) {
  const typers = useChannelTypers(channelId, me);
  const line = useMemo(
    () =>
      typingLine(
        typers.map((id) => {
          const entry = directory.get(id);
          return chatPersonName(id, entry ? [entry] : undefined);
        }),
      ),
    [typers, directory],
  );
  return (
    <div aria-live="polite" className="min-h-5 min-w-0 shrink-0 px-4 md:px-5">
      {line !== null ? (
        <p
          data-testid={CHAT_TEST_IDS.typingIndicator}
          className="min-w-0 text-xs leading-5 break-words text-muted-foreground"
        >
          {line}
        </p>
      ) : null}
    </div>
  );
}
