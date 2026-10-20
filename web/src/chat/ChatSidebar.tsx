/**
 * Joined channels and direct messages.
 *
 * Direct messages stay on screen when the list is empty. A channel name
 * renders with one leading `#`.
 */
import type { ChatChannel } from "./chatApi";
import { CHAT_TEST_IDS } from "./testids";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";

export interface ChatSidebarProps {
  channels: ChatChannel[];
  dms: ChatChannel[];
  activeChannelId?: string;
  /** Under `md` the list fills the width on `/chat` and hides once a channel is open. */
  className?: string;
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
  return (
    <li>
      <Link
        to={`/chat/${encodeURIComponent(channel.id)}`}
        componentId={componentId}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex min-h-11 min-w-0 items-center break-words rounded-md px-3 py-3 text-sm hover:bg-muted md:block md:min-h-0 md:py-1.5",
          active && "bg-[var(--sidebar-active)] text-[var(--sidebar-active-foreground)]",
        )}
      >
        {/* A flex item's min-content ignores `break-words`; the span lets one long word wrap too. */}
        <span className="min-w-0 break-words">{label}</span>
      </Link>
    </li>
  );
}

export function ChatSidebar({ channels, dms, activeChannelId, className }: ChatSidebarProps) {
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
        <h2 className="px-3 pb-1 pt-3 text-xs font-medium text-muted-foreground">
          Direktnachrichten
        </h2>
        <ul>
          {dms.map((channel) => (
            <EntryLink
              key={channel.id}
              channel={channel}
              label={dmLabel(channel)}
              active={channel.id === activeChannelId}
              componentId="chat.dm"
            />
          ))}
        </ul>
      </section>
    </aside>
  );
}
