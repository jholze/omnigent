/**
 * Settings section "Agenten".
 *
 * The form collects a display name and a handle. The ogc_ token and the
 * webhook secret exist only in `revealed` and are dropped when the dialog
 * closes, so a later render cannot show them again.
 */
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SettingsGroup } from "@/components/SettingsGroup";
import { copyText } from "@/lib/clipboard";
import {
  agentAccountActivity,
  ChatApiError,
  createAgentAccount,
  fetchAgentAccounts,
  revokeAgentAccount,
  rotateAgentAccount,
  type AgentAccountIssued,
  type AgentAccountRecord,
} from "../chatApi";

const HANDLE_PATTERN = /^[a-z0-9][a-z0-9-]{1,39}$/;
const ONCE_SENTENCE = "Token und Secret sind nur einmal sichtbar.";

function failureText(error: unknown, fallback: string): string {
  if (error instanceof ChatApiError && error.status === 409) {
    return "Das Handle ist schon vergeben oder das Kontolimit ist erreicht.";
  }
  if (error instanceof ChatApiError && error.status === 404) {
    return "Das Agentenkonto wurde nicht gefunden.";
  }
  return fallback;
}

function SecretLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-start gap-2">
        <code
          className="min-w-0 flex-1 text-xs break-all"
          data-testid={label === "Token" ? "agent-account-token" : "agent-account-secret"}
        >
          {value}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          componentId={
            label === "Token"
              ? "settings.agent_accounts.copy_token"
              : "settings.agent_accounts.copy_secret"
          }
          onClick={() => {
            void copyText(value).catch(() => undefined);
          }}
        >
          Kopieren
        </Button>
      </div>
    </div>
  );
}

export function AgentAccountsSection() {
  const [accounts, setAccounts] = useState<AgentAccountRecord[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [handle, setHandle] = useState("");
  const [revealed, setRevealed] = useState<AgentAccountIssued | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchAgentAccounts(controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setAccounts(rows);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(failureText(err, "Agentenkonten konnten nicht geladen werden."));
      });
    return () => controller.abort();
  }, []);

  function dismissSecrets() {
    setRevealed(null);
  }

  function showIssued(issued: AgentAccountIssued, next: AgentAccountRecord[]) {
    setAccounts(next);
    setRevealed(issued);
    setError(null);
  }

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = displayName.trim();
    const nextHandle = handle.trim();
    if (name.length < 1 || name.length > 40) {
      setError("Der Anzeigename braucht 1 bis 40 Zeichen.");
      return;
    }
    if (!HANDLE_PATTERN.test(nextHandle)) {
      setError("Das Handle muss klein sein und 2 bis 40 Zeichen haben.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const issued = await createAgentAccount({ display_name: name, handle: nextHandle });
      showIssued(issued, [...accounts, issued.account]);
      setDisplayName("");
      setHandle("");
    } catch (err: unknown) {
      setError(failureText(err, "Das Agentenkonto konnte nicht angelegt werden."));
    } finally {
      setPending(false);
    }
  }

  async function onRotate(accountId: string) {
    setPending(true);
    setError(null);
    try {
      const issued = await rotateAgentAccount(accountId);
      showIssued(
        issued,
        accounts.map((account) => (account.id === issued.account.id ? issued.account : account)),
      );
    } catch (err: unknown) {
      setError(failureText(err, "Das Token konnte nicht rotiert werden."));
    } finally {
      setPending(false);
    }
  }

  async function onRevoke(accountId: string) {
    setPending(true);
    setError(null);
    try {
      await revokeAgentAccount(accountId);
      setAccounts((rows) => rows.filter((account) => account.id !== accountId));
      setRevealed((current) => (current?.account.id === accountId ? null : current));
    } catch (err: unknown) {
      setError(failureText(err, "Das Agentenkonto konnte nicht widerrufen werden."));
    } finally {
      setPending(false);
    }
  }

  return (
    <section data-testid="agent-accounts-section">
      <h1 className="settings-page-title text-2xl font-semibold">Agenten</h1>
      <p className="mt-1 text-ui text-muted-foreground" data-testid="agent-accounts-hint">
        Der Grok-Bot sollte chat_unread mindestens alle 5 Minuten abfragen.
      </p>
      <div className="mt-6 flex flex-col gap-6">
        <SettingsGroup title="Neues Agentenkonto">
          <form className="flex flex-col gap-3" onSubmit={(event) => void onCreate(event)}>
            <label className="flex flex-col gap-1 text-sm" htmlFor="agent-account-display-name">
              Anzeigename
              <Input
                id="agent-account-display-name"
                value={displayName}
                maxLength={40}
                autoComplete="off"
                componentId="settings.agent_accounts.display_name"
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm" htmlFor="agent-account-handle">
              Handle
              <Input
                id="agent-account-handle"
                value={handle}
                maxLength={40}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                componentId="settings.agent_accounts.handle"
                onChange={(event) => setHandle(event.target.value)}
              />
            </label>
            <div>
              <Button type="submit" disabled={pending} componentId="settings.agent_accounts.create">
                Anlegen
              </Button>
            </div>
          </form>
        </SettingsGroup>
        <SettingsGroup title="Deine Agentenkonten">
          {accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">Noch keine Agentenkonten.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {accounts.map((account) => (
                <li
                  key={account.id}
                  className="flex flex-wrap items-center gap-2"
                  data-testid="agent-account-row"
                >
                  <span className="min-w-0 font-medium break-words">{account.display_name}</span>
                  <span className="text-sm text-muted-foreground">@{account.handle}</span>
                  <span className="text-sm text-muted-foreground">
                    {agentAccountActivity(account.last_seen_at)}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={pending}
                    componentId="settings.agent_accounts.rotate"
                    aria-label={`Token rotieren für ${account.handle}`}
                    onClick={() => void onRotate(account.id)}
                  >
                    Token rotieren
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={pending}
                    componentId="settings.agent_accounts.revoke"
                    aria-label={`Widerrufen ${account.handle}`}
                    onClick={() => void onRevoke(account.id)}
                  >
                    Widerrufen
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </SettingsGroup>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <Dialog
        open={revealed !== null}
        onOpenChange={(open) => {
          if (!open) dismissSecrets();
        }}
      >
        {revealed ? (
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Einmalig sichtbar</DialogTitle>
              <DialogDescription>{ONCE_SENTENCE}</DialogDescription>
            </DialogHeader>
            <SecretLine label="Token" value={revealed.token} />
            <SecretLine label="Secret" value={revealed.webhook_secret} />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                componentId="settings.agent_accounts.dismiss"
                onClick={dismissSecrets}
              >
                Schließen
              </Button>
            </DialogFooter>
          </DialogContent>
        ) : null}
      </Dialog>
    </section>
  );
}
