import { Columns2Icon, MessagesSquareIcon, PanelRightIcon, SearchIcon } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { ChatComposer, ComposerSendButton } from "@/components/composer/ChatComposer";
import { Button } from "@/components/ui/button";
import { subscribeHumanChat } from "@/lib/humanChat";
import { authenticatedFetch, getCurrentUserId } from "@/lib/identity";

/** One stored line. `created_at` is Unix epoch microseconds. */
interface ChatLine {
  id: string;
  created_by: string | null;
  created_at: number;
  body: string;
}

interface ChatList {
  items: ChatLine[];
  shared: boolean;
}

type Placement = "panel" | "split";

interface SessionChatState {
  conversationId: string | null;
  status: "loading" | "hidden" | "shown";
  items: ChatLine[];
  draft: string;
  unread: boolean;
  tip: string | null;
  placement: Placement;
  /** Pixel width of the split column. Kept while the chat sits in the panel. */
  dockWidth: number;
  viewing: boolean;
  /** Session ids with an unseen people-chat line. The value is the latest body, or "". */
  unreadById: Record<string, string>;
}

const PLACEHOLDER = "Message the people in this session";
const PLACEMENT_KEY = "omnigent:session-chat-placement";
const WIDTH_KEY = "omnigent:session-chat-width";
const DEFAULT_WIDTH = 320;

function readPlacement(): Placement {
  try {
    return localStorage.getItem(PLACEMENT_KEY) === "split" ? "split" : "panel";
  } catch {
    return "panel";
  }
}

function readWidth(): number {
  try {
    const width = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(width) && width >= 280 && width <= 640) return width;
  } catch {
    // A blocked storage read keeps the default width.
  }
  return DEFAULT_WIDTH;
}

let state: SessionChatState = {
  conversationId: null,
  status: "loading",
  items: [],
  draft: "",
  unread: false,
  tip: null,
  placement: readPlacement(),
  dockWidth: readWidth(),
  viewing: false,
  unreadById: {},
};

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function replace(next: SessionChatState): void {
  state = next;
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getState(): SessionChatState {
  return state;
}

export function useSessionChat(): SessionChatState {
  return useSyncExternalStore(subscribe, getState, getState);
}

export function useSessionChatPlacement(): Placement {
  return useSyncExternalStore(
    subscribe,
    () => state.placement,
    () => state.placement,
  );
}

export function setSessionChatPlacement(placement: Placement): void {
  if (state.placement === placement) return;
  try {
    localStorage.setItem(PLACEMENT_KEY, placement);
  } catch {
    // The choice still applies for this page load.
  }
  if (placement === "split" && state.conversationId) forgetUnread(state.conversationId);
  replace({ ...state, placement });
}

export function setSessionChatDockWidth(width: number, persist = false): void {
  const next = Math.min(640, Math.max(280, Math.round(width)));
  if (state.dockWidth !== next) replace({ ...state, dockWidth: next });
  if (!persist) return;
  try {
    localStorage.setItem(WIDTH_KEY, String(state.dockWidth));
  } catch {
    // The dragged width still holds until reload.
  }
}

/** Width the header must leave clear when the chat is docked beside the agent. */
export function useSessionChatDockOffset(): number {
  return useSyncExternalStore(subscribe, dockOffset, dockOffset);
}

function dockOffset(): number {
  return state.placement === "split" && state.status === "shown" ? state.dockWidth : 0;
}

export function setSessionChatViewing(viewing: boolean): void {
  if (state.viewing === viewing && (!viewing || (!state.unread && state.tip === null))) return;
  if (!viewing) {
    replace({ ...state, viewing });
    return;
  }
  const conversationId = state.conversationId;
  const unreadById = { ...state.unreadById };
  if (conversationId) delete unreadById[conversationId];
  replace({ ...state, viewing: true, unread: false, tip: null, unreadById });
}

export function markSessionChatRead(): void {
  if (state.conversationId) forgetUnread(state.conversationId);
}

/** Drop the singleton between tests. Placement and width stay, they come from storage. */
export function resetSessionChatStore(): void {
  replace({
    conversationId: null,
    status: "loading",
    items: [],
    draft: "",
    unread: false,
    tip: null,
    placement: state.placement,
    dockWidth: state.dockWidth,
    viewing: false,
    unreadById: {},
  });
}

/** Latest unseen people-chat line for a sidebar row, or null when that session is caught up. */
export function useSessionChatRowUnreadTip(conversationId: string): string | null {
  return useSyncExternalStore(
    subscribe,
    () => (conversationId in state.unreadById ? state.unreadById[conversationId] : null),
    () => null,
  );
}

function rememberUnread(conversationId: string, tip: string | null): void {
  const nextTip = tip ?? state.unreadById[conversationId] ?? "";
  const sameTip = state.unreadById[conversationId] === nextTip;
  const current = state.conversationId === conversationId;
  if (sameTip && (!current || (state.unread && state.tip === (tip ?? state.tip)))) return;
  replace({
    ...state,
    unreadById: { ...state.unreadById, [conversationId]: nextTip },
    ...(current ? { unread: true, tip: tip ?? state.tip } : {}),
  });
}

function forgetUnread(conversationId: string): void {
  if (!(conversationId in state.unreadById) && state.conversationId !== conversationId) return;
  if (
    !(conversationId in state.unreadById) &&
    !state.unread &&
    state.tip === null
  ) {
    return;
  }
  const unreadById = { ...state.unreadById };
  delete unreadById[conversationId];
  replace({
    ...state,
    unreadById,
    ...(state.conversationId === conversationId ? { unread: false, tip: null } : {}),
  });
}

function asLine(value: unknown): ChatLine | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== "string" || typeof row.body !== "string") return null;
  if (typeof row.created_at !== "number" || !Number.isFinite(row.created_at)) return null;
  return {
    id: row.id,
    body: row.body,
    created_at: row.created_at,
    created_by: typeof row.created_by === "string" ? row.created_by : null,
  };
}

function parseList(value: unknown): ChatList | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  if (typeof body.shared !== "boolean" || !Array.isArray(body.items)) return null;
  const items: ChatLine[] = [];
  for (const raw of body.items) {
    const line = asLine(raw);
    if (line) items.push(line);
  }
  return { items, shared: body.shared };
}

function isVisible(list: ChatList): boolean {
  return list.shared || list.items.length > 0;
}

async function readChat(conversationId: string): Promise<ChatList | null> {
  try {
    const res = await authenticatedFetch(`/v1/sessions/${encodeURIComponent(conversationId)}/chat`);
    if (!res.ok) return null;
    return parseList(await res.json());
  } catch {
    return null;
  }
}

function formatChatTime(createdAtUs: number): string {
  const date = new Date(createdAtUs / 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function applyList(conversationId: string, list: ChatList | null): void {
  if (state.conversationId !== conversationId) return;
  if (!list || !isVisible(list)) {
    if (state.status === "hidden" && state.items.length === 0) return;
    const unreadById = { ...state.unreadById };
    delete unreadById[conversationId];
    replace({
      ...state,
      status: "hidden",
      items: [],
      draft: "",
      unread: false,
      tip: null,
      unreadById,
    });
    return;
  }
  replace({ ...state, status: "shown", items: list.items });
}

/**
 * Human chat for the people in one shared session.
 *
 * The header button opens it as a tab in the right workspace panel. Split
 * docks that same transcript between the agent column and the panel, so a
 * wide screen can show the agent, the people, and files at once. The control
 * stays hidden until GET /chat says the root is shared or already has lines.
 */
export function SessionChatColumn({
  conversationId,
  onOpen,
}: {
  conversationId: string;
  onOpen?: () => void;
}) {
  const genRef = useRef(0);

  useEffect(() => {
    const gen = ++genRef.current;
    const savedTip = state.unreadById[conversationId];
    replace({
      ...state,
      conversationId,
      status: "loading",
      items: [],
      draft: "",
      unread: savedTip !== undefined,
      tip: savedTip ? savedTip : null,
    });

    void readChat(conversationId).then((list) => {
      if (genRef.current !== gen) return;
      applyList(conversationId, list);
    });

    const unsubscribe = subscribeHumanChat((signal) => {
      if (signal.conversationId !== conversationId) return;
      if (!state.viewing && state.placement !== "split") {
        rememberUnread(conversationId, null);
      }
      void readChat(conversationId).then((list) => {
        if (genRef.current !== gen) return;
        applyList(conversationId, list);
        if (state.viewing || state.placement === "split" || !list) return;
        const last = list.items[list.items.length - 1];
        if (last) rememberUnread(conversationId, last.body);
      });
    });
    return () => {
      genRef.current += 1;
      unsubscribe();
    };
  }, [conversationId]);

  const snap = useSessionChat();
  if (snap.status !== "shown" || snap.conversationId !== conversationId) return null;

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label="Session chat"
      title={snap.unread && snap.tip ? snap.tip : undefined}
      onClick={() => {
        markSessionChatRead();
        onOpen?.();
      }}
      className="relative text-muted-foreground hover:text-foreground"
    >
      <MessagesSquareIcon className="size-3.5" />
      {snap.unread && (
        <span
          data-testid="session-chat-unread"
          className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-primary"
          aria-hidden="true"
        />
      )}
    </Button>
  );
}

export function SessionChatPane({
  onSplit,
  onMoveToPanel,
}: {
  onSplit?: () => void;
  onMoveToPanel?: () => void;
}) {
  const snap = useSessionChat();
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [snap.items]);

  const send = async (text: string) => {
    const conversationId = state.conversationId;
    const body = text.trim();
    if (!conversationId || !body) return;
    try {
      const res = await authenticatedFetch(
        `/v1/sessions/${encodeURIComponent(conversationId)}/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body }),
        },
      );
      if (state.conversationId !== conversationId || !res.ok) return;
      const created = asLine(await res.json());
      if (!created || state.conversationId !== conversationId) return;
      replace({
        ...state,
        draft: "",
        items: state.items.some((item) => item.id === created.id)
          ? state.items
          : [...state.items, created],
      });
    } catch {
      // Leave the draft. Nothing was stored.
    }
  };

  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const raw = query.trim().toLowerCase();
    if (!raw) return snap.items;
    const from = raw.startsWith("from:");
    const needle = (from ? raw.slice("from:".length) : raw).trim();
    if (!needle) return snap.items;
    return snap.items.filter((item) => {
      const who = (item.created_by ?? "").toLowerCase();
      if (from) return who.includes(needle);
      return item.body.toLowerCase().includes(needle) || who.includes(needle);
    });
  }, [query, snap.items]);
  const viewerId = getCurrentUserId();

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(snap.draft);
  };

  return (
    <div data-testid="session-human-chat-column" className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
        <span className="text-ui font-medium">Session chat</span>
        {onSplit && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Split beside the agent"
            title="Split beside the agent"
            onClick={onSplit}
          >
            <Columns2Icon className="size-3.5" />
          </Button>
        )}
        {onMoveToPanel && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Move into the panel"
            title="Move into the panel"
            onClick={onMoveToPanel}
          >
            <PanelRightIcon className="size-3.5" />
          </Button>
        )}
      </div>
      <label className="mx-3 mt-3 flex h-8 shrink-0 items-center gap-2 rounded-lg border border-border px-2 text-muted-foreground focus-within:border-ring">
        <SearchIcon className="size-3.5 shrink-0" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search, or from:ada"
          aria-label="Search this session chat"
          className="w-full bg-transparent text-ui text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>
      <ul ref={listRef} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
        {visible.map((item) => {
          const mine = viewerId !== null && item.created_by === viewerId;
          const who = item.created_by ?? "Someone";
          const when = (
            <time dateTime={new Date(item.created_at / 1000).toISOString()}>
              {formatChatTime(item.created_at)}
            </time>
          );
          return (
            <li key={item.id} className="flex flex-col gap-0.5">
              {mine ? (
                <Message from="user">
                  <div className="flex items-baseline justify-end gap-2 text-xs text-muted-foreground">
                    <span className="truncate">{who}</span>
                    {when}
                  </div>
                  <MessageContent>
                    <p className="whitespace-pre-wrap">{item.body}</p>
                  </MessageContent>
                </Message>
              ) : (
                <>
                  <div className="flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
                    <span className="truncate">{who}</span>
                    {when}
                  </div>
                  <p className="whitespace-pre-wrap text-ui">{item.body}</p>
                </>
              )}
            </li>
          );
        })}
        {visible.length === 0 && (
          <li className="py-6 text-ui text-muted-foreground">No messages yet.</li>
        )}
      </ul>
      <form className="border-t border-border p-2" onSubmit={onSubmit}>
        <ChatComposer
          className="min-h-0 md:min-h-0"
          keyboard={{ submitWithModEnter: false, preventsKeyboardSubmit: false }}
          input={{
            value: snap.draft,
            placeholder: PLACEHOLDER,
            onChange: (event) => replace({ ...state, draft: event.target.value }),
            onKeyDown: (event, intent) => {
              if (!intent.shouldSubmitFromKeyboard) return;
              event.preventDefault();
              void send(event.currentTarget.value);
            },
          }}
          actions={{
            leading: null,
            trailing: <ComposerSendButton label="Send" disabled={!snap.draft.trim()} />,
          }}
        />
      </form>
    </div>
  );
}

/** The people chat docked between the agent column and the workspace panel. */
export function SessionChatDock({ onMoveToPanel }: { onMoveToPanel: () => void }) {
  const snap = useSessionChat();
  const widthRef = useRef(snap.dockWidth);
  widthRef.current = snap.dockWidth;

  const onResizePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = widthRef.current;
    const onMove = (move: PointerEvent) => {
      setSessionChatDockWidth(startWidth + startX - move.clientX);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setSessionChatDockWidth(widthRef.current, true);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  if (snap.placement !== "split" || snap.status !== "shown") return null;

  return (
    <aside
      data-testid="session-human-chat-dock"
      aria-label="Session chat"
      className="relative hidden min-h-0 shrink-0 flex-col border-l border-border bg-card md:flex"
      style={{ width: snap.dockWidth }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize session chat"
        onPointerDown={onResizePointerDown}
        className="absolute inset-y-0 left-0 z-10 w-1 cursor-col-resize hover:bg-primary/30"
      />
      <SessionChatPane onMoveToPanel={onMoveToPanel} />
    </aside>
  );
}
