/**
 * Open agent requests in a channel (FR-047): one strip per Agent-Thread whose
 * agent needs an answer ("braucht Antwort", an open approval).
 *
 * The strip sits above the scrolling message list, so it stays in view after
 * the thread's root row scrolls away. It names the thread (root message text)
 * and opens it. It renders only while the chip state is `waiting`; once the
 * approval is resolved the chip changes and the strip is gone. Ordinary
 * threads never get one.
 *
 * Navigation is an explicit `navigate()` on click, not the analytics Link
 * wrapper's default handling; the `<a href>` stays for open-in-new-tab.
 * Static, no motion until the motion tokens land (T-117).
 */
import { ChevronRightIcon, MessageCircleQuestionMarkIcon } from "lucide-react";

import { Link, useNavigate } from "@/lib/routing";
import type { DirectoryEntry } from "../chatApi";
import { mentionLabel, splitMessageBody } from "../MessageItem";
import { CHAT_TEST_IDS } from "../testids";
import { useAgentThreadChip } from "./useAgentThreadChip";

const NAME_MAX_CHARS = 160;

/** Root message as one plain line: mentions as `@Name`, whitespace collapsed. */
export function threadNameOf(body: string, directory: Map<string, DirectoryEntry>): string {
  const text = splitMessageBody(body)
    .map((segment) =>
      segment.type === "mention" ? mentionLabel(segment, directory) : segment.text,
    )
    .join("")
    .replace(/\s+/g, " ")
    .trim();
  if (text === "") return "Agent-Thread";
  return text.length > NAME_MAX_CHARS ? `${text.slice(0, NAME_MAX_CHARS - 1).trimEnd()}…` : text;
}

export interface PendingAgentRequestRoot {
  rootId: string;
  body: string;
  href: string;
}

function PendingAgentRequest({
  root,
  directory,
}: {
  root: PendingAgentRequestRoot;
  directory: Map<string, DirectoryEntry>;
}) {
  const navigate = useNavigate();
  const state = useAgentThreadChip(root.rootId);
  if (state?.kind !== "waiting") return null;
  const name = threadNameOf(root.body, directory);
  return (
    <li className="border-b border-warning/50 bg-warning/15">
      <Link
        to={root.href}
        data-testid={CHAT_TEST_IDS.pendingRequest}
        data-root-id={root.rootId}
        onClick={(event) => {
          if (
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey ||
            event.button !== 0
          ) {
            return;
          }
          event.preventDefault();
          navigate(root.href);
        }}
        className="flex min-h-11 w-full min-w-0 items-center gap-2 px-4 py-1.5 text-sm text-foreground hover:bg-warning/10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring md:min-h-9"
      >
        <MessageCircleQuestionMarkIcon className="size-4 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 text-pretty wrap-anywhere">
          <span className="font-medium">Agent braucht Antwort</span>
          <span className="text-foreground"> in </span>
          <span>{name}</span>
        </span>
        <span className="shrink-0 text-xs text-foreground max-md:sr-only">Thread öffnen</span>
        <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </Link>
    </li>
  );
}

export function PendingAgentRequests({
  roots,
  directory,
}: {
  roots: PendingAgentRequestRoot[];
  directory: Map<string, DirectoryEntry>;
}) {
  if (roots.length === 0) return null;
  return (
    <ul
      aria-label="Offene Agent-Anfragen"
      className="max-h-[33dvh] shrink-0 overflow-y-auto empty:hidden"
    >
      {roots.map((root) => (
        <PendingAgentRequest key={root.rootId} root={root} directory={directory} />
      ))}
    </ul>
  );
}
