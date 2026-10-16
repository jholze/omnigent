import { MessagesSquareIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { subscribeHumanChat } from "@/lib/humanChat";
import { authenticatedFetch } from "@/lib/identity";

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

const PLACEHOLDER = "Message the people in this session";
const AGENT_COMPOSER_LABEL = "Message the agent";

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

function focusAgentComposer(): void {
  document.querySelector<HTMLElement>(`[aria-label="${AGENT_COMPOSER_LABEL}"]`)?.focus();
}

/**
 * Human chat beside one shared agent session.
 *
 * The control is hidden until GET /chat says the root is shared or already
 * has lines. Enter posts to /chat. Escape and Close return focus to the
 * agent composer and do not write into it. A line that arrives while the
 * column is closed sets a client-only dot; the tooltip text comes from a
 * follow-up GET, because the stream event has no body.
 */
export function SessionChatColumn({ conversationId }: { conversationId: string }) {
  const [status, setStatus] = useState<"loading" | "hidden" | "shown">("loading");
  const [items, setItems] = useState<ChatLine[]>([]);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [unread, setUnread] = useState(false);
  const [tip, setTip] = useState<string | null>(null);
  const openRef = useRef(false);
  const genRef = useRef(0);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const gen = ++genRef.current;
    openRef.current = false;
    // A session switch drops the previous column. The setters return the
    // current value on the first mount, so ChatHeader's render stays quiet.
    setOpen((current) => (current ? false : current));
    setUnread((current) => (current ? false : current));
    setTip((current) => (current === null ? current : null));
    setDraft((current) => (current === "" ? current : ""));
    setStatus((current) => (current === "loading" ? current : "loading"));
    setItems((current) => (current.length === 0 ? current : []));

    const apply = (list: ChatList | null) => {
      if (genRef.current !== gen) return;
      if (!list || !isVisible(list)) {
        // A failed or empty read stays on the initial null render. Writing
        // state here re-renders ChatHeader after its tests have finished.
        setOpen(false);
        setUnread(false);
        setTip(null);
        setDraft("");
        setStatus((current) => (current === "shown" ? "hidden" : current));
        setItems((current) => (current.length === 0 ? current : []));
        return;
      }
      setItems(list.items);
      setStatus("shown");
    };

    void readChat(conversationId).then(apply);

    const unsubscribe = subscribeHumanChat((signal) => {
      if (signal.conversationId !== conversationId) return;
      if (!openRef.current) setUnread(true);
      void readChat(conversationId).then((list) => {
        if (genRef.current !== gen) return;
        if (list && isVisible(list)) {
          setItems(list.items);
          setStatus("shown");
        }
        if (openRef.current || !list) return;
        const last = list.items[list.items.length - 1];
        if (last) setTip(last.body);
      });
    });
    return () => {
      genRef.current += 1;
      unsubscribe();
    };
  }, [conversationId]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [open, items]);

  if (status !== "shown") return null;

  const closeColumn = () => {
    openRef.current = false;
    setOpen(false);
    focusAgentComposer();
  };

  const toggleColumn = () => {
    if (openRef.current) {
      closeColumn();
      return;
    }
    openRef.current = true;
    setOpen(true);
    setUnread(false);
    setTip(null);
    const gen = genRef.current;
    void readChat(conversationId).then((list) => {
      if (genRef.current !== gen || !list || !isVisible(list)) return;
      setItems(list.items);
    });
  };

  const send = async (text: string) => {
    const body = text.trim();
    if (!body) return;
    const gen = genRef.current;
    try {
      const res = await authenticatedFetch(
        `/v1/sessions/${encodeURIComponent(conversationId)}/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body }),
        },
      );
      if (genRef.current !== gen || !res.ok) return;
      const created = asLine(await res.json());
      if (genRef.current !== gen) return;
      setDraft("");
      if (!created) return;
      setItems((prev) => (prev.some((item) => item.id === created.id) ? prev : [...prev, created]));
    } catch {
      // Leave the draft. Nothing was stored.
    }
  };

  const onColumnKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closeColumn();
  };

  const onDraftKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void send(event.currentTarget.value);
  };

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label="Session chat"
        aria-expanded={open}
        title={unread && tip ? tip : undefined}
        onClick={toggleColumn}
        className="relative text-muted-foreground hover:text-foreground"
      >
        <MessagesSquareIcon className="size-3.5" />
        {unread && (
          <span
            data-testid="session-chat-unread"
            className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-primary"
            aria-hidden="true"
          />
        )}
      </Button>
      {open && (
        <aside
          data-testid="session-human-chat-column"
          aria-label="Session chat"
          onKeyDown={onColumnKeyDown}
          className="fixed top-14 bottom-20 z-40 flex w-72 flex-col rounded-lg border border-border bg-popover text-popover-foreground shadow-lg right-[calc(var(--workspace-panel-offset,0px)+0.75rem)]"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
            <span className="text-ui font-medium">Session chat</span>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="Close"
              onClick={closeColumn}
            >
              <XIcon className="size-3.5" />
            </Button>
          </div>
          <ul
            ref={listRef}
            className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3"
          >
            {items.map((item) => (
              <li key={item.id} className="flex flex-col gap-0.5">
                <div className="flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
                  <span className="truncate">{item.created_by ?? "Someone"}</span>
                  <time dateTime={new Date(item.created_at / 1000).toISOString()}>
                    {formatChatTime(item.created_at)}
                  </time>
                </div>
                <p className="whitespace-pre-wrap text-ui">{item.body}</p>
              </li>
            ))}
          </ul>
          <form
            className="border-t border-border p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void send(draft);
            }}
          >
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onDraftKeyDown}
              placeholder={PLACEHOLDER}
              rows={2}
              className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5 text-ui outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </form>
        </aside>
      )}
    </>
  );
}
