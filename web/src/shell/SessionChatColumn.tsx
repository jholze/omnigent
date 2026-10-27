/**
 * Session chat column (FR-029, AC-029a): the human side chat of a root
 * session, opened from the ChatHeader.
 *
 * Uses the same rows and composer as team-chat channels (MessageList,
 * ChatComposerCard) on top of `GET/POST /v1/sessions/{id}/chat`. The server
 * decides access: 403 for a public-link visitor or an admin post, 404 for no
 * grant or an Agent-Thread session (AC-029c). The column shows that answer
 * and never works around it.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlertIcon, MessageCircleIcon, XIcon } from "lucide-react";
import * as DialogPrimitive from "radix-ui/dialog";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  ChatApiError,
  fetchBootstrap,
  fetchSessionChat,
  postSessionChat,
  teamChatQueryKeys,
  type SessionChatLine,
  type SessionChatList,
} from "@/chat/chatApi";
import { ChatComposerCard } from "@/chat/ChatComposerCard";
import { directoryMap, MessagesSkeleton } from "@/chat/ChannelView";
import { MessageList } from "@/chat/MessageList";
import type { MessageView } from "@/chat/MessageItem";
import { CHAT_TEST_IDS } from "@/chat/testids";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { getCurrentUserId } from "@/lib/identity";
import { cn } from "@/lib/utils";

/** Poll while open: the session chat has no live frames on this server build. */
const REFRESH_MS = 15_000;

interface PendingLine {
  clientId: string;
  body: string;
  createdAtMs: number;
  status: "sending" | "failed";
}

function lineView(sessionId: string, line: SessionChatLine): MessageView {
  return {
    key: line.id,
    id: line.id,
    clientId: null,
    channelId: sessionId,
    threadRootId: null,
    authorId: line.created_by ?? "",
    authorKind: "user",
    // `created_at` is epoch microseconds.
    createdAtMs: Math.floor(line.created_at / 1000),
    body: line.body,
    status: "sent",
  };
}

function pendingView(sessionId: string, me: string, line: PendingLine): MessageView {
  return {
    key: `client:${line.clientId}`,
    id: null,
    clientId: line.clientId,
    channelId: sessionId,
    threadRootId: null,
    authorId: me,
    authorKind: "user",
    createdAtMs: line.createdAtMs,
    body: line.body,
    status: line.status,
  };
}

/** German text for a refused or failed load. Status codes stay the server's. */
export function sessionChatErrorText(error: unknown): string {
  if (error instanceof ChatApiError) {
    if (error.status === 403) return "Der Session-Chat ist für dich hier nicht freigegeben.";
    if (error.status === 404) return "Für diese Session gibt es keinen Session-Chat.";
  }
  return "Session-Chat konnte nicht geladen werden.";
}

function newClientId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface SessionChatColumnProps {
  sessionId: string;
  /** Session title, for the accessible names. */
  title: string;
  id: string;
  onClose: () => void;
}

export function SessionChatColumn({ sessionId, title, id, onClose }: SessionChatColumnProps) {
  const queryClient = useQueryClient();
  const isMobile = useIsMobileViewport();
  const titleId = useId();
  const sectionRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const me = getCurrentUserId() ?? "";
  const [pending, setPending] = useState<PendingLine[]>([]);
  const [postError, setPostError] = useState<string | null>(null);

  const chat = useQuery({
    queryKey: teamChatQueryKeys.sessionChat(sessionId),
    queryFn: ({ signal }) => fetchSessionChat(sessionId, signal),
    retry: false,
    refetchInterval: (query) => (query.state.error ? false : REFRESH_MS),
  });
  // Names for the rows. Shared with /chat; failure just leaves ids.
  const bootstrap = useQuery({
    queryKey: teamChatQueryKeys.bootstrap,
    queryFn: ({ signal }) => fetchBootstrap(signal),
    retry: false,
    staleTime: 60_000,
  });
  const directory = useMemo(() => directoryMap(bootstrap.data?.directory ?? []), [bootstrap.data]);
  const viewer = bootstrap.data?.me ?? me;

  const views = useMemo(
    () => [
      ...(chat.data?.items ?? []).map((line) => lineView(sessionId, line)),
      ...pending.map((line) => pendingView(sessionId, viewer, line)),
    ],
    [chat.data, pending, sessionId, viewer],
  );

  // Desktop (non-modal panel): move focus into the column on open, to the
  // composer when there is one, else the heading. The phone dialog does this
  // through the Radix primitive (onOpenAutoFocus below).
  useEffect(() => {
    if (isMobile) return;
    const node = sectionRef.current;
    if (!node) return;
    const input = node.querySelector<HTMLTextAreaElement>("textarea");
    (input ?? headingRef.current)?.focus();
  }, [isMobile]);

  // Desktop: Escape closes the column, like the thread pane. On the phone the
  // dialog primitive owns Escape.
  useEffect(() => {
    if (isMobile) return;
    const node = sectionRef.current;
    if (!node) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    node.addEventListener("keydown", onKeyDown);
    return () => node.removeEventListener("keydown", onKeyDown);
  }, [isMobile, onClose]);

  const deliver = (line: PendingLine) => {
    setPostError(null);
    postSessionChat(sessionId, line.body).then(
      (stored) => {
        queryClient.setQueryData<SessionChatList>(
          teamChatQueryKeys.sessionChat(sessionId),
          (current) =>
            current && !current.items.some((item) => item.id === stored.id)
              ? { ...current, items: [...current.items, stored] }
              : current,
        );
        setPending((list) => list.filter((item) => item.clientId !== line.clientId));
      },
      (error: unknown) => {
        setPending((list) =>
          list.map((item) =>
            item.clientId === line.clientId ? { ...item, status: "failed" } : item,
          ),
        );
        if (error instanceof ChatApiError && error.status === 403) {
          setPostError("Du kannst hier lesen, aber nicht schreiben.");
        }
      },
    );
  };

  const send = (body: string): boolean => {
    const trimmed = body.trim();
    if (trimmed === "") return false;
    const line: PendingLine = {
      clientId: newClientId(),
      body: trimmed,
      createdAtMs: Date.now(),
      status: "sending",
    };
    setPending((list) => [...list, line]);
    deliver(line);
    return true;
  };

  const retry = (clientId: string) => {
    const line = pending.find((item) => item.clientId === clientId);
    if (!line) return;
    const next = { ...line, status: "sending" as const };
    setPending((list) => list.map((item) => (item.clientId === clientId ? next : item)));
    deliver(next);
  };

  let body;
  if (chat.isPending) {
    body = <MessagesSkeleton />;
  } else if (chat.isError) {
    const refused =
      chat.error instanceof ChatApiError &&
      (chat.error.status === 403 || chat.error.status === 404);
    body = (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p
          role="alert"
          data-testid={CHAT_TEST_IDS.sessionColumnError}
          className="flex max-w-full items-start gap-2 text-sm text-pretty break-words text-muted-foreground"
        >
          <CircleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0">{sessionChatErrorText(chat.error)}</span>
        </p>
        {refused ? null : (
          <Button
            variant="outline"
            componentId="session_chat.retry"
            className="min-h-11 px-4"
            loading={chat.isFetching}
            onClick={() => void chat.refetch()}
          >
            Erneut versuchen
          </Button>
        )}
      </div>
    );
  } else {
    body = (
      <MessageList
        key={sessionId}
        messages={views}
        directory={directory}
        me={viewer}
        label={`Session-Chat zu ${title}`}
        onRetry={retry}
        empty={
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-1 p-6 text-center">
            <p className="text-sm text-foreground">Noch keine Nachrichten</p>
            <p className="text-xs text-pretty text-muted-foreground">
              Nur für Menschen mit Zugriff auf diese Session. Der Agent liest hier nicht mit.
            </p>
          </div>
        }
      />
    );
  }

  const heading = (
    <h2
      ref={headingRef}
      id={isMobile ? undefined : titleId}
      tabIndex={-1}
      className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium outline-none"
    >
      <MessageCircleIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="shrink-0">Session-Chat</span>
      <span className="min-w-0 truncate text-xs font-normal text-muted-foreground">{title}</span>
    </h2>
  );

  const content = (
    <>
      <header className="flex min-h-11 shrink-0 items-center gap-2 border-b border-border py-1 pr-1 pl-4">
        {/* Phone: the heading is the dialog's title (Radix wires aria-labelledby). */}
        {isMobile ? <DialogPrimitive.Title asChild>{heading}</DialogPrimitive.Title> : heading}
        <button
          type="button"
          aria-label="Session-Chat schließen"
          title="Session-Chat schließen (Esc)"
          onClick={onClose}
          className="relative flex size-11 min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md text-muted-foreground before:absolute before:-inset-1.5 before:content-[''] hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring md:size-8 md:min-h-8 md:min-w-8"
        >
          <XIcon className="size-4" aria-hidden="true" />
        </button>
      </header>
      {body}
      {postError ? (
        <p role="alert" className="flex items-center gap-1 px-4 pt-1 text-xs text-destructive">
          <CircleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
          {postError}
        </p>
      ) : null}
      {chat.isError ? null : (
        <ChatComposerCard
          channelId={sessionId}
          me={viewer}
          placeholder="Nachricht an alle in dieser Session…"
          label={`Nachricht im Session-Chat zu ${title}`}
          onSend={send}
          mentions={false}
        />
      )}
    </>
  );

  if (isMobile) {
    // Phone: the column covers the screen, so it is a modal dialog. The Radix
    // primitive moves focus in, traps it, closes on Escape and hides the page
    // behind from assistive tech (A11Y-01/A11Y-03, CODE-01). Focus return to
    // the header toggle is done by SessionChatToggle after unmount.
    return (
      <Dialog
        open
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
      >
        <DialogContent
          id={id}
          data-testid={CHAT_TEST_IDS.sessionColumn}
          aria-describedby={undefined}
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            // Heading, not the composer: no soft keyboard popping up on open.
            event.preventDefault();
            headingRef.current?.focus();
          }}
          onCloseAutoFocus={(event) => event.preventDefault()}
          className="inset-0 top-0 left-0 flex h-full max-h-full w-full max-w-full min-h-0 min-w-0 translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-none border-0 bg-background p-0 pt-[env(safe-area-inset-top)] text-foreground shadow-none duration-0 sm:max-w-full data-open:animate-none data-open:zoom-in-100 data-closed:animate-none data-closed:zoom-out-100"
          // Override the primitive's iOS centering so the sheet stays full-screen,
          // and drop its zoom-in-95 enter animation: a full-screen sheet must not
          // scale, or the 44px touch targets inside measure < 44px (NFR-032).
          // Inline so it wins over the primitive's animate-in class regardless
          // of CSS order.
          style={{ top: 0, maxHeight: "100%", animation: "none", transform: "none" }}
        >
          {content}
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <section
      ref={sectionRef}
      id={id}
      aria-labelledby={titleId}
      data-testid={CHAT_TEST_IDS.sessionColumn}
      className="absolute top-full right-2 mt-1 flex h-[min(40rem,calc(100dvh-4.5rem))] min-h-0 w-[min(24rem,calc(100vw-1rem))] min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-background text-foreground shadow-lg md:right-4"
    >
      {content}
    </section>
  );
}

interface SessionChatToggleProps {
  sessionId: string;
  title: string;
}

/**
 * ChatHeader entry for the session chat. Owns the open state, so AppShell
 * only decides whether the session has a chat at all (not an Agent-Thread).
 */
export function SessionChatToggle({ sessionId, title }: SessionChatToggleProps) {
  const [open, setOpen] = useState(false);
  const columnId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  const label = open ? "Session-Chat schließen" : "Session-Chat öffnen";

  // Another session: start closed.
  const [shownFor, setShownFor] = useState(sessionId);
  if (shownFor !== sessionId) {
    setShownFor(sessionId);
    setOpen(false);
  }

  const close = () => {
    returnFocus.current = true;
    setOpen(false);
  };

  // Return focus to the toggle after the column has unmounted, so the phone
  // dialog's focus trap is gone first.
  useEffect(() => {
    if (open || !returnFocus.current) return;
    returnFocus.current = false;
    toggleRef.current?.focus();
  }, [open]);

  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={toggleRef}
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={label}
            aria-expanded={open}
            aria-controls={open ? columnId : undefined}
            data-testid={CHAT_TEST_IDS.sessionColumnToggle}
            componentId="chat.header.session_chat"
            onClick={() => setOpen((value) => !value)}
            className={cn(
              // 24 px glyph button, 44 px hit area (UI gate).
              "relative border-none text-muted-foreground before:absolute before:-inset-2.5 before:content-[''] hover:text-foreground max-md:size-11 max-md:before:inset-0",
              open && "bg-muted text-foreground",
            )}
          >
            <MessageCircleIcon className="size-4 max-md:size-5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">Session-Chat</TooltipContent>
      </Tooltip>
      {open ? (
        <SessionChatColumn sessionId={sessionId} title={title} id={columnId} onClose={close} />
      ) : null}
    </>
  );
}
