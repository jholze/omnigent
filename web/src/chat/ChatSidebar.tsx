/**
 * Joined channels and direct messages.
 *
 * Direct messages stay on screen when the list is empty. A channel name
 * renders with one leading `#`. Unread and mention counts come from
 * bootstrap, which is refetched after `read.updated` (FR-008, FR-009).
 */
import type { ReactNode } from "react";

import type { ChatChannel } from "./chatApi";
import { CountBadge } from "./CountBadge";
import { CHAT_TEST_IDS } from "./testids";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";

export interface ChatSidebarProps {
  channels: ChatChannel[];
  dms: ChatChannel[];
  activeChannelId?: string;
  /** Under `md` the list fills the width on `/chat` and hides once a channel is open. */
  className?: string;
  /** Control next to the "Direktnachrichten" heading (new DM). */
  dmAction?: ReactNode;
  /** Resolved titles for DMs, keyed by channel id. Missing ids use `dmLabel`. */
  dmTitles?: ReadonlyMap<string, string>;
}

/** True for `/chat`, `/chat/:channelId`, and `/chat/:channelId/t/:threadRootId`. */
export function isTeamChatPathname(pathname: string, chatRoot = "/chat"): boolean {
  const root = chatRoot.endsWith("/") ? chatRoot.slice(0, -1) : chatRoot;
  if (pathname === root || pathname === `${root}/`) return true;
  if (!pathname.startsWith(`${root}/`)) return false;
  const rest = pathname
    .slice(root.length + 1)
    .split("/")
    .filter(Boolean);
  if (rest.length === 1) return true;
  return rest.length === 3 && rest[1] === "t";
}

export function channelLabel(name: string | null | undefined): string {
  if (name == null || name === "") return "#";
  return name.startsWith("#") ? name : `#${name}`;
}

export function dmLabel(channel: ChatChannel): string {
  if (channel.name != null && channel.name !== "") return channel.name;
  return "Direktnachricht";
}

function count(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/** Sidebar pill size; CountBadge supplies the text, cap and pulse. */
const SIDEBAR_BADGE_CLASS = "h-5 min-w-5 px-1.5 text-xs";

/** "#general, 3 ungelesen, 1 Erwähnung". Plain label when nothing is unread. */
export function entryAccessibleName(label: string, unread: number, mentions: number): string {
  const parts = [label];
  if (unread > 0) parts.push(`${unread} ungelesen`);
  if (mentions > 0) parts.push(`${mentions} ${mentions === 1 ? "Erwähnung" : "Erwähnungen"}`);
  return parts.join(", ");
}

function EntryLink({
  channel,
  label,
  active,
  componentId,
}: {
  channel: ChatChannel;
  label: string;
  active: boolean;
  componentId: string;
}) {
  const unread = count(channel.unread_count);
  const mentions = count(channel.mention_count);
  const hasUnread = unread > 0 || mentions > 0;
  return (
    <li>
      <Link
        to={`/chat/${encodeURIComponent(channel.id)}`}
        componentId={componentId}
        aria-current={active ? "page" : undefined}
        aria-label={hasUnread ? entryAccessibleName(label, unread, mentions) : undefined}
        data-unread={unread}
        data-mentions={mentions}
        className={cn(
          "flex min-h-11 min-w-0 items-center gap-2 break-words rounded-md px-3 py-3 text-sm hover:bg-muted md:min-h-0 md:py-1.5",
          hasUnread && !active && "font-medium text-foreground",
          active && "bg-[var(--sidebar-active)] text-[var(--sidebar-active-foreground)]",
        )}
      >
        {/* A flex item's min-content ignores `break-words`; the span lets one long word wrap too. */}
        <span className="min-w-0 flex-1 break-words">{label}</span>
        {/* Decorative: the link's aria-label already speaks both counts. */}
        <CountBadge
          count={mentions}
          prefix="@"
          tone="accent"
          testId={CHAT_TEST_IDS.mentionBadge}
          className={SIDEBAR_BADGE_CLASS}
        />
        <CountBadge
          count={unread}
          testId={CHAT_TEST_IDS.unreadBadge}
          className={cn(SIDEBAR_BADGE_CLASS, "bg-muted-foreground/15 text-foreground")}
        />
      </Link>
    </li>
  );
}

export function ChatSidebar({
  channels,
  dms,
  activeChannelId,
  className,
  dmAction,
  dmTitles,
}: ChatSidebarProps) {
  return (
    <aside
      data-testid={CHAT_TEST_IDS.sidebar}
      className={cn(
        "flex shrink-0 flex-col overflow-y-auto max-md:w-full md:w-60 md:border-r md:border-border",
        className,
      )}
    >
      <section aria-label="Channels">
        <h2 className="px-3 pb-1 pt-3 text-xs font-medium text-muted-foreground">Channels</h2>
        <ul>
          {channels.map((channel) => (
            <EntryLink
              key={channel.id}
              channel={channel}
              label={channelLabel(channel.name)}
              active={channel.id === activeChannelId}
              componentId="chat.channel"
            />
          ))}
        </ul>
      </section>
      <section aria-label="Direktnachrichten">
        <div className="flex items-center justify-between gap-2 pr-1">
          <h2 className="px-3 pb-1 pt-3 text-xs font-medium text-muted-foreground">
            Direktnachrichten
          </h2>
          {dmAction}
        </div>
        <ul>
          {dms.map((channel) => (
            <EntryLink
              key={channel.id}
              channel={channel}
              label={dmTitles?.get(channel.id) ?? dmLabel(channel)}
              active={channel.id === activeChannelId}
              componentId="chat.dm"
            />
          ))}
        </ul>
      </section>
    </aside>
  );
}
