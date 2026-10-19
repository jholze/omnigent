/**
 * Team chat shell: `/chat`, `/chat/:channelId`, `/chat/:channelId/t/:threadRootId`.
 *
 * The message list, composer, and thread pane are later tasks. With
 * `team_chat` off, the page explains that and does not call `/v1/chat/*`.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { fetchBootstrap, teamChatQueryKeys, type ChatChannel } from "./chatApi";
import { ChatSidebar, channelLabel, dmLabel } from "./ChatSidebar";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";
import { useChatFrames } from "./useChatFrames";

function channelsOf(channels: ChatChannel[] | undefined): ChatChannel[] {
  return (channels ?? []).filter((channel) => channel.id !== "");
}

export function ChatPage() {
  const info = useServerInfo();
  const enabled = isFeatureEnabled(info, "team_chat");
  const { channelId } = useParams<{ channelId?: string }>();
  const bootstrap = useQuery({
    queryKey: teamChatQueryKeys.bootstrap,
    queryFn: ({ signal }) => fetchBootstrap(signal),
    enabled,
  });
  useChatFrames(enabled);
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

  return (
    <div data-testid={CHAT_TEST_IDS.page} className={rootClassName}>
      <ChatSidebar
        channels={channels}
        dms={dms}
        activeChannelId={channelId}
        className={channelId ? "max-md:hidden" : undefined}
      />
      <div className={cn("flex min-w-0 flex-1 flex-col", !channelId && "max-md:hidden")}>
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
      </div>
    </div>
  );
}
