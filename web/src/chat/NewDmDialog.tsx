/**
 * "Neue Direktnachricht": pick one person from the bootstrap directory.
 *
 * `POST /v1/chat/dms` returns the same channel id for the same people (201
 * created, 200 existing; AC-021a). The channel is written into the bootstrap
 * cache, then a fresh bootstrap is fetched, before navigating. An older
 * in-flight bootstrap must not be the one that lands.
 */
import { useMemo, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { BotIcon, PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useNavigate } from "@/lib/routing";
import {
  fetchBootstrap,
  openDm,
  teamChatQueryKeys,
  type BootstrapResponse,
  type ChatChannel,
  type DirectoryEntry,
} from "./chatApi";
import { fetchLatest } from "./freshQuery";
import { principalLabel } from "./MessageItem";
import { CHAT_TEST_IDS } from "./testids";

/** List the DM at once, so the route can resolve before the refetch returns. */
function rememberDm(queryClient: QueryClient, channel: ChatChannel): void {
  queryClient.setQueryData<BootstrapResponse>(teamChatQueryKeys.bootstrap, (current) => {
    if (current === undefined) return current;
    if (current.dms.some((dm) => dm.id === channel.id)) return current;
    return { ...current, dms: [...current.dms, channel] };
  });
}

export interface NewDmDialogProps {
  me: string;
  directory: DirectoryEntry[];
}

export function NewDmDialog({ me, directory }: NewDmDialogProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const byId = useMemo(() => new Map(directory.map((entry) => [entry.id, entry])), [directory]);
  const people = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("de-DE");
    return directory
      .filter((entry) => entry.id !== "" && entry.id !== me)
      .map((entry) => ({ entry, label: principalLabel(entry.id, byId) }))
      .filter(
        ({ entry, label }) =>
          needle === "" ||
          label.toLocaleLowerCase("de-DE").includes(needle) ||
          entry.id.toLocaleLowerCase("de-DE").includes(needle),
      )
      .sort((a, b) => a.label.localeCompare(b.label, "de-DE"));
  }, [directory, me, query, byId]);

  const start = async (principalId: string) => {
    setPendingId(principalId);
    setFailed(false);
    try {
      const channel = await openDm([principalId]);
      rememberDm(queryClient, channel);
      await fetchLatest(queryClient, teamChatQueryKeys.bootstrap, (signal) =>
        fetchBootstrap(signal),
      );
      setOpen(false);
      navigate(`/chat/${encodeURIComponent(channel.id)}`);
    } catch {
      setFailed(true);
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setQuery("");
          setFailed(false);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          componentId="chat.dm.new"
          aria-label="Neue Direktnachricht"
          title="Neue Direktnachricht"
          data-testid={CHAT_TEST_IDS.newDm}
          className="text-muted-foreground hover:text-foreground max-md:size-11"
        >
          <PlusIcon className="size-4" aria-hidden="true" />
        </Button>
      </DialogTrigger>
      <DialogContent data-testid={CHAT_TEST_IDS.newDmDialog}>
        <DialogHeader>
          <DialogTitle>Neue Direktnachricht</DialogTitle>
          <DialogDescription>Wähle, mit wem du schreiben willst.</DialogDescription>
        </DialogHeader>
        <Input
          type="search"
          value={query}
          placeholder="Name suchen"
          aria-label="Name suchen"
          className="max-md:min-h-11"
          onChange={(event) => setQuery(event.target.value)}
        />
        {people.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {query.trim() === "" ? "Noch niemand sonst im Chat." : "Niemand gefunden."}
          </p>
        ) : (
          <ul aria-label="Personen" className="-mx-2 flex max-h-72 flex-col overflow-y-auto">
            {people.map(({ entry, label }) => (
              // Phone rows are 48px: the shell dialog opens from 95% scale, so a
              // 44px row would measure under 44px until the zoom settles.
              <li key={entry.id} className="shrink-0">
                <Button
                  type="button"
                  variant="ghost"
                  componentId="chat.dm.person"
                  data-testid={CHAT_TEST_IDS.newDmPerson}
                  data-principal-id={entry.id}
                  loading={pendingId === entry.id}
                  disabled={pendingId !== null && pendingId !== entry.id}
                  className="h-auto min-h-12 w-full justify-start gap-2 px-2 py-2 text-left whitespace-normal md:min-h-9"
                  onClick={() => void start(entry.id)}
                >
                  {entry.kind === "agent_account" ? (
                    <BotIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                  ) : null}
                  <span className="min-w-0 flex-1 break-words">{label}</span>
                  {entry.kind === "agent_account" ? (
                    <span className="shrink-0 text-xs text-muted-foreground">Agentenkonto</span>
                  ) : null}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {failed ? (
          <p
            role="alert"
            data-testid={CHAT_TEST_IDS.newDmError}
            className="text-sm text-destructive"
          >
            Direktnachricht konnte nicht geöffnet werden. Versuch es noch einmal.
          </p>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
