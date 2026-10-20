/**
 * Team chat shell: `/chat`, `/chat/:channelId`, `/chat/:channelId/t/:threadRootId`.
 *
 * Channel column: message list and composer. Thread route: thread pane right
 * of it (desktop) or instead of it (phone). With `team_chat` off, the page
 * explains that and does not call `/v1/chat/*`.
 */
import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  fetchBootstrap,
  teamChatQueryKeys,
  type BootstrapResponse,
  type ChatChannel,
} from "./chatApi";
import { ChannelView, directoryMap } from "./ChannelView";
import { ChatSidebar, channelLabel, dmLabel } from "./ChatSidebar";
import { NewDmDialog } from "./NewDmDialog";
import { ThreadPane } from "./ThreadPane";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";
import { useChatFrames } from "./useChatFrames";

function channelsOf(channels: ChatChannel[] | undefined): ChatChannel[] {
  return (channels ?? []).filter((channel) => channel.id !== "");
}

/**
 * A message in a channel the sidebar does not list (a DM someone just opened
 * with me) refetches bootstrap once per channel (AC-021a).
 */
function useBootstrapForNewChannels(enabled: boolean, data: BootstrapResponse | undefined): void {
  const queryClient = useQueryClient();
  const changes = useTeamChatStore((state) => state.changes);
  const requestedRef = useRef(new Set<string>());
  useEffect(() => {
    if (!enabled || data === undefined) return;
    const known = new Set([...data.channels, ...data.dms].map((channel) => channel.id));
    let stale = false;
    for (const change of Object.values(changes)) {
      if (change.kind !== "message" || known.has(change.channel_id)) continue;
      if (requestedRef.current.has(change.channel_id)) continue;
      requestedRef.current.add(change.channel_id);
      stale = true;
    }
    if (stale) void queryClient.invalidateQueries({ queryKey: teamChatQueryKeys.bootstrap });
  }, [enabled, data, changes, queryClient]);
}

export function ChatPage() {
  const info = useServerInfo();
  const enabled = isFeatureEnabled(info, "team_chat");
  const { channelId, threadRootId } = useParams<{ channelId?: string; threadRootId?: string }>();
  const bootstrap = useQuery({
    queryKey: teamChatQueryKeys.bootstrap,
    queryFn: ({ signal }) => fetchBootstrap(signal),
    enabled,
  });
  useChatFrames(enabled);
  useBootstrapForNewChannels(enabled, bootstrap.data);
  const directory = useMemo(
    () => directoryMap(bootstrap.data?.directory ?? []),
    [bootstrap.data?.directory],
  );
  useEffect(() => {
    if (!enabled) return;
    const cursor = bootstrap.data?.changes_cursor;
    if (cursor) useTeamChatStore.getState().seedCursor(cursor);
  }, [enabled, bootstrap.data]);

  if (info === "loading") {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <Spinner className="size-5 text-muted-foreground" aria-label="Loading" />
      </div>
    );
  }
  if (!enabled) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Chat ist auf diesem Server nicht aktiviert
      </div>
    );
  }

  const rootClassName = "flex min-h-0 w-full min-w-0 flex-1 overflow-x-hidden";

  if (bootstrap.isPending) {
    return (
      <div
        data-testid={CHAT_TEST_IDS.page}
        aria-busy="true"
        className={cn(rootClassName, "items-center justify-center")}
      >
        <Spinner className="size-5 text-muted-foreground" aria-label="Lädt…" />
      </div>
    );
  }
  if (bootstrap.isError) {
    return (
      <div
        data-testid={CHAT_TEST_IDS.page}
        className={cn(rootClassName, "flex-col items-center justify-center gap-3 p-6 text-center")}
      >
        <p
          role="alert"
          data-testid={CHAT_TEST_IDS.bootstrapError}
          className="text-sm text-muted-foreground"
        >
          Chat konnte nicht geladen werden
        </p>
        <Button
          variant="outline"
          componentId="chat.bootstrap.retry"
          data-testid={CHAT_TEST_IDS.bootstrapRetry}
          className="min-h-11 px-4"
          loading={bootstrap.isFetching}
          onClick={() => void bootstrap.refetch()}
        >
          Erneut versuchen
        </Button>
      </div>
    );
  }

  const channels = channelsOf(bootstrap.data?.channels);
  const dms = channelsOf(bootstrap.data?.dms);
  const selectedChannel = channels.find((channel) => channel.id === channelId);
  const selectedDm = dms.find((channel) => channel.id === channelId);
  let heading: string | null = null;
  if (selectedChannel) heading = channelLabel(selectedChannel.name);
  else if (selectedDm) heading = dmLabel(selectedDm);
  const missing = Boolean(channelId) && heading === null;
  const me = bootstrap.data?.me ?? "";
  const openChannelId = channelId && heading !== null ? channelId : null;
  const threadOpen = openChannelId !== null && Boolean(threadRootId);

  return (
    <div data-testid={CHAT_TEST_IDS.page} className={rootClassName}>
      <ChatSidebar
        channels={channels}
        dms={dms}
        activeChannelId={channelId}
        // Tablet with a thread open: channel and thread get the width.
        className={cn(channelId && "max-md:hidden", threadOpen && "md:max-lg:hidden")}
        dmAction={<NewDmDialog me={me} directory={bootstrap.data?.directory ?? []} />}
      />
      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-1 flex-col",
          !channelId && "max-md:hidden",
          // Phone: the thread pane replaces the channel column.
          threadOpen && "max-md:hidden",
        )}
      >
        {channelId ? (
          <Link
            to="/chat"
            componentId="chat.back"
            data-testid={CHAT_TEST_IDS.back}
            className="flex min-h-11 items-center gap-1 border-b border-border px-3 text-sm text-muted-foreground hover:text-foreground md:hidden"
          >
            <ChevronLeftIcon className="size-4 shrink-0" aria-hidden="true" />
            Channels
          </Link>
        ) : null}
        {heading ? (
          <h1 className="min-w-0 break-words border-b border-border px-4 py-3 text-sm font-medium">
            {heading}
          </h1>
        ) : null}
        {missing ? (
          <p
            data-testid={CHAT_TEST_IDS.channelMissing}
            className="p-6 text-center text-sm text-muted-foreground"
          >
            Channel nicht gefunden
          </p>
        ) : null}
        {openChannelId !== null && heading !== null ? (
          <ChannelView
            key={openChannelId}
            channelId={openChannelId}
            label={heading}
            me={me}
            directory={directory}
          />
        ) : null}
      </div>
      {threadOpen && openChannelId !== null && heading !== null && threadRootId ? (
        <ThreadPane
          key={`${openChannelId}:${threadRootId}`}
          channelId={openChannelId}
          threadRootId={threadRootId}
          channelLabel={heading}
          me={me}
          directory={directory}
        />
      ) : null}
    </div>
  );
}
