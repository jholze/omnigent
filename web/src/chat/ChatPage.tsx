/**
 * Team chat shell: `/chat`, `/chat/:channelId`, `/chat/:channelId/t/:threadRootId`.
 *
 * Channel column: message list and composer. Thread route: thread pane right
 * of it (desktop) or instead of it (phone). With `team_chat` off, the page
 * explains that and does not call `/v1/chat/*`.
 */
import { useEffect, useMemo, useRef } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeftIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { isFeatureEnabled } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { Link, useParams } from "@/lib/routing";
import { cn } from "@/lib/utils";
import {
  ChatApiError,
  fetchBootstrap,
  listChannelMembers,
  teamChatQueryKeys,
  type BootstrapResponse,
  type ChatChannel,
} from "./chatApi";
import { ChannelView, directoryMap } from "./ChannelView";
import "./chatScale.css";
import { ChatSidebar, channelLabel, dmLabel } from "./ChatSidebar";
import { dmHeading, dmParticipantLine } from "./dmTitle";
import { MotionRoot } from "./MotionRoot";
import { NewDmDialog } from "./NewDmDialog";
import { ThreadPane } from "./ThreadPane";
import { CHAT_TEST_IDS } from "./testids";
import { useTeamChatStore } from "./teamChatStore";
import { fetchLatest } from "./freshQuery";
import { requestChatBackfill, useChatFrames } from "./useChatFrames";

function channelsOf(channels: ChatChannel[] | undefined): ChatChannel[] {
  return (channels ?? []).filter((channel) => channel.id !== "");
}

/** Bootstrap fetches per new-channel frame before the next frame has to retry. */
const NEW_CHANNEL_BOOTSTRAP_ATTEMPTS = 3;
const NEW_CHANNEL_RETRY_DELAY_MS = 400;

function bootstrapChannelIds(data: BootstrapResponse): Set<string> {
  return new Set([...data.channels, ...data.dms].map((channel) => channel.id));
}

/**
 * A message in a channel the sidebar does not list (a DM someone just opened
 * with me) refetches bootstrap until the response lists it (AC-021a).
 *
 * `fetchLatest` cancels an older in-flight bootstrap, which would otherwise
 * hide the new DM. A channel id counts as done only once a response contains
 * it. A failed or short round leaves it open: the next frame for that
 * channel starts a new round. One round per frame, so no loop.
 */
function useBootstrapForNewChannels(enabled: boolean, data: BootstrapResponse | undefined): void {
  const queryClient = useQueryClient();
  const changes = useTeamChatStore((state) => state.changes);
  const confirmedRef = useRef(new Set<string>());
  // Channel id -> message count (changes are merged by id) a round already ran for.
  const attemptedRef = useRef(new Map<string, number>());
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useEffect(() => {
    if (!enabled || data === undefined) return;
    const known = bootstrapChannelIds(data);
    for (const id of known) confirmedRef.current.add(id);
    const newest = new Map<string, number>();
    for (const change of Object.values(changes)) {
      if (change.kind !== "message" || known.has(change.channel_id)) continue;
      if (confirmedRef.current.has(change.channel_id)) continue;
      newest.set(change.channel_id, (newest.get(change.channel_id) ?? 0) + 1);
    }
    const wanted = [...newest].flatMap(([channelId, marker]) => {
      if (attemptedRef.current.get(channelId) === marker) return [];
      attemptedRef.current.set(channelId, marker);
      return [channelId];
    });
    if (wanted.length === 0) return;
    const attempt = (n: number): void => {
      if (!mountedRef.current) return;
      fetchLatest(queryClient, teamChatQueryKeys.bootstrap, (signal) => fetchBootstrap(signal))
        .then((fresh) => {
          const listed = bootstrapChannelIds(fresh);
          for (const id of wanted) if (listed.has(id)) confirmedRef.current.add(id);
        })
        .catch((error: unknown) => {
          const status = error instanceof ChatApiError ? String(error.status) : "error";
          console.warn(`[team-chat] bootstrap refetch for a new channel failed (${status})`);
        })
        .finally(() => {
          if (wanted.every((id) => confirmedRef.current.has(id))) return;
          if (n >= NEW_CHANNEL_BOOTSTRAP_ATTEMPTS) return;
          setTimeout(() => attempt(n + 1), NEW_CHANNEL_RETRY_DELAY_MS * n);
        });
    };
    attempt(1);
  }, [enabled, data, changes, queryClient]);
}

/** Motion root around the whole chat so every `m.*` has `LazyMotion` above it (T-117). */
export function ChatPage() {
  return (
    <MotionRoot>
      <ChatPageContent />
    </MotionRoot>
  );
}

function ChatPageContent() {
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
  const queryClient = useQueryClient();
  const heldChannelHeading = useRef<{ id: string; heading: string } | null>(null);
  // The updates socket is the fast path. A dropped frame still has to surface
  // a new DM, so poll the changes feed while the page is open (AC-021a).
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => requestChatBackfill(queryClient), 2_000);
    return () => clearInterval(timer);
  }, [enabled, queryClient]);
  const directory = useMemo(
    () => directoryMap(bootstrap.data?.directory ?? []),
    [bootstrap.data?.directory],
  );
  const dmChannels = useMemo(
    () => channelsOf(bootstrap.data?.dms).filter((channel) => channel.kind === "dm"),
    [bootstrap.data?.dms],
  );
  const memberQueries = useQueries({
    queries: dmChannels.map((dm) => ({
      queryKey: teamChatQueryKeys.members(dm.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => listChannelMembers(dm.id, signal),
      enabled: enabled && dm.id !== "",
      staleTime: 30_000,
    })),
  });
  const meId = bootstrap.data?.me ?? "";
  const dmTitles = useMemo(() => {
    const titles = new Map<string, string>();
    dmChannels.forEach((dm, index) => {
      const members = memberQueries[index]?.data;
      if (!members) return;
      titles.set(dm.id, dmHeading(meId, members, directory));
    });
    return titles;
  }, [directory, dmChannels, meId, memberQueries]);
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

  // The shell header floats over the page (absolute, h-14, md:h-12). Start
  // team chat below it, or the thread close control sits under that header
  // and the click never reaches it. Same offset as the extension host.
  const rootClassName = "flex min-h-0 w-full min-w-0 flex-1 overflow-x-hidden pt-14 md:pt-12";

  // A failed or cancelled refetch must not replace a page that already has
  // data: that unmounts the open DM and drops text the user just typed.
  if (bootstrap.isPending && bootstrap.data === undefined) {
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
  if (bootstrap.isError && bootstrap.data === undefined) {
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
  const selectedMembers = selectedDm
    ? memberQueries[dmChannels.findIndex((dm) => dm.id === selectedDm.id)]?.data
    : undefined;
  let heading: string | null = null;
  if (selectedChannel) heading = channelLabel(selectedChannel.name);
  else if (selectedDm) heading = dmTitles.get(selectedDm.id) ?? dmLabel(selectedDm);
  const participantLine =
    selectedDm && selectedMembers ? dmParticipantLine(meId, selectedMembers, directory) : "";
  const me = bootstrap.data?.me ?? "";
  // A bootstrap fetched before this DM existed can land later and omit it.
  // Keep the heading we already showed so the composer stays mounted (AC-021a).
  if (channelId && heading !== null) {
    heldChannelHeading.current = { id: channelId, heading };
  }
  const visibleHeading =
    heading ??
    (channelId !== undefined && heldChannelHeading.current?.id === channelId
      ? heldChannelHeading.current.heading
      : null);
  const missing = Boolean(channelId) && visibleHeading === null;
  const openChannelId = channelId && visibleHeading !== null ? channelId : null;
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
        dmTitles={dmTitles}
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
        {visibleHeading ? (
          <div className="min-w-0 border-b border-border px-4 py-3">
            <h1 className="min-w-0 break-words text-sm font-medium">{visibleHeading}</h1>
            {participantLine !== "" ? (
              <p className="break-words text-sm text-muted-foreground">{participantLine}</p>
            ) : null}
          </div>
        ) : null}
        {missing ? (
          <p
            data-testid={CHAT_TEST_IDS.channelMissing}
            className="p-6 text-center text-sm text-muted-foreground"
          >
            Channel nicht gefunden
          </p>
        ) : null}
        {openChannelId !== null && visibleHeading !== null ? (
          <ChannelView
            key={openChannelId}
            channelId={openChannelId}
            label={visibleHeading}
            me={me}
            directory={directory}
          />
        ) : null}
      </div>
      {threadOpen && openChannelId !== null && visibleHeading !== null && threadRootId ? (
        <ThreadPane
          key={`${openChannelId}:${threadRootId}`}
          channelId={openChannelId}
          threadRootId={threadRootId}
          channelLabel={visibleHeading}
          me={me}
          directory={directory}
        />
      ) : null}
    </div>
  );
}
