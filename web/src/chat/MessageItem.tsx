/**
 * One chat message row.
 *
 * Human bodies render as text nodes only (FR-020, NFR-015): no HTML, no
 * Markdown. `http(s)` URLs become links with `rel="noopener noreferrer"`, and
 * `<@kind:id>` tokens become name chips. Agent and bot bodies are Markdown
 * through AI Elements `MessageResponse` (streamdown-security). System lines
 * (server notices such as a failed agent start) are one muted text line.
 */
import type { ReactNode } from "react";
import { BotIcon, CircleAlertIcon, InfoIcon, MessageSquareIcon } from "lucide-react";

import { MessageResponse } from "@/components/ai-elements/message";
import { Button } from "@/components/ui/button";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";
import { AgentBotBadge } from "./BotBadge";
import type { DirectoryEntry } from "./chatApi";
import { CHAT_TEST_IDS } from "./testids";

export type MessageDeliveryStatus = "sent" | "sending" | "failed";

/** What a row needs, for stored messages and optimistic sends alike. */
export interface MessageView {
  /** Stable React key: message id, or `client:<client_id>` before the server answers. */
  key: string;
  id: string | null;
  clientId: string | null;
  channelId: string;
  threadRootId: string | null;
  authorId: string;
  authorKind: string;
  createdAtMs: number;
  body: string;
  status: MessageDeliveryStatus;
  /**
   * A failed send answered 429 (AC-017a): nothing was stored. Seconds from
   * `Retry-After`, or null when the server sent none.
   */
  rateLimited?: { retryAfterSeconds: number | null } | null;
}

/** "Zu viele Nachrichten …" hint for a 429 send. Names Retry-After when known. */
export function rateLimitHint(retryAfterSeconds: number | null): string {
  if (retryAfterSeconds === null) return "Zu viele Nachrichten, bitte gleich erneut senden";
  return `Zu viele Nachrichten, erneut senden in ${retryAfterSeconds} s`;
}

export type BodySegment =
  | { type: "text"; text: string }
  | { type: "link"; href: string; text: string }
  | { type: "mention"; kind: string; target: string; raw: string };

const MENTION_RE = /<@(user|agent_account|omnigent_agent):([^>\s]+)>/g;
const URL_RE = /https?:\/\/[^\s<>"'`]+/gi;
const TRAILING_PUNCTUATION = new Set([".", ",", ";", ":", "!", "?", "'", '"', ")", "]", "}"]);

function count(text: string, char: string): number {
  return text.split(char).length - 1;
}

/** Strip sentence punctuation after a URL; keep a `)` that closes one inside it. */
function trimUrl(raw: string): string {
  let url = raw;
  while (url.length > 0 && TRAILING_PUNCTUATION.has(url.at(-1) ?? "")) {
    if (url.endsWith(")") && count(url, "(") >= count(url, ")")) break;
    url = url.slice(0, -1);
  }
  return url;
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function pushText(segments: BodySegment[], text: string): void {
  if (text === "") return;
  const last = segments.at(-1);
  if (last?.type === "text") last.text += text;
  else segments.push({ type: "text", text });
}

function splitLinks(text: string, segments: BodySegment[]): void {
  let cursor = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    const url = trimUrl(match[0]);
    if (url === "" || !isHttpUrl(url)) continue;
    pushText(segments, text.slice(cursor, start));
    segments.push({ type: "link", href: url, text: url });
    cursor = start + url.length;
  }
  pushText(segments, text.slice(cursor));
}

/**
 * Split a body into text, `http(s)` links and mention tokens. Everything else,
 * `javascript:` URLs and HTML included, stays plain text.
 */
export function splitMessageBody(body: string): BodySegment[] {
  const segments: BodySegment[] = [];
  let cursor = 0;
  for (const match of body.matchAll(MENTION_RE)) {
    const start = match.index ?? 0;
    splitLinks(body.slice(cursor, start), segments);
    segments.push({ type: "mention", kind: match[1], target: match[2], raw: match[0] });
    cursor = start + match[0].length;
  }
  splitLinks(body.slice(cursor), segments);
  return segments;
}

function segmentLength(segment: BodySegment): number {
  return segment.type === "mention" ? segment.raw.length : segment.text.length;
}

/** Segments keyed by their offset in the body: stable while the body is unchanged. */
function keyedSegments(body: string): { key: string; segment: BodySegment }[] {
  let offset = 0;
  return splitMessageBody(body).map((segment) => {
    const key = `${segment.type}:${offset}`;
    offset += segmentLength(segment);
    return { key, segment };
  });
}

/** Bootstrap `display_name` for a principal, else its handle (agents), else the id. */
export function principalLabel(id: string, directory: Map<string, DirectoryEntry>): string {
  const entry = directory.get(id);
  const name = entry?.display_name?.trim();
  if (name) return name;
  const handle = entry?.handle?.trim();
  if (handle) return handle;
  return id;
}

export function mentionLabel(
  segment: Extract<BodySegment, { type: "mention" }>,
  directory: Map<string, DirectoryEntry>,
): string {
  if (segment.kind === "omnigent_agent") return `@${segment.target}`;
  return `@${principalLabel(segment.target, directory)}`;
}

export function MessageBody({
  body,
  directory,
  className,
}: {
  body: string;
  directory: Map<string, DirectoryEntry>;
  className?: string;
}) {
  return (
    <p className={cn("text-sm text-pretty whitespace-pre-wrap wrap-anywhere", className)}>
      {keyedSegments(body).map(({ key, segment }) => {
        if (segment.type === "link") {
          return (
            <a
              key={key}
              href={segment.href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline underline-offset-2 hover:no-underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
            >
              {segment.text}
            </a>
          );
        }
        if (segment.type === "mention") {
          return (
            <span
              key={key}
              data-testid={CHAT_TEST_IDS.mentionChip}
              title={segment.raw}
              className="rounded-sm bg-muted px-1 font-medium text-foreground"
            >
              {mentionLabel(segment, directory)}
            </span>
          );
        }
        return <span key={key}>{segment.text}</span>;
      })}
    </p>
  );
}

const timeFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayTimeFormat = new Intl.DateTimeFormat("de-DE", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const fullFormat = new Intl.DateTimeFormat("de-DE", { dateStyle: "full", timeStyle: "short" });
const replyPlural = new Intl.PluralRules("de-DE");
const numberFormat = new Intl.NumberFormat("de-DE");

/** Server timestamps are epoch microseconds; accept ms and s too. */
export function toEpochMs(value: number): number {
  if (value > 1e14) return Math.floor(value / 1000);
  if (value < 1e11) return value * 1000;
  return value;
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function formatMessageTime(ms: number, now = new Date()): string {
  const date = new Date(ms);
  return isSameDay(date, now) ? timeFormat.format(date) : dayTimeFormat.format(date);
}

/** "1 Antwort", "2 Antworten", "1.284 Antworten". */
export function replyCountLabel(n: number): string {
  const noun = replyPlural.select(n) === "one" ? "Antwort" : "Antworten";
  return `${numberFormat.format(n)} ${noun}`;
}

export function isAgentKind(kind: string): boolean {
  return kind === "agent_account" || kind === "omnigent_agent" || kind === "agent";
}

export function MessageAvatar({ label, agent }: { label: string; agent: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-medium",
        agent ? "bg-secondary text-secondary-foreground" : "bg-muted text-muted-foreground",
      )}
    >
      {agent ? <BotIcon className="size-4" /> : (label.trim().at(0)?.toUpperCase() ?? "?")}
    </span>
  );
}

export interface MessageItemProps {
  message: MessageView;
  directory: Map<string, DirectoryEntry>;
  /** Consecutive message by the same author: no avatar and name line. */
  compact?: boolean;
  /** Top-level rows in the channel link to their thread; thread rows do not. */
  threadHref?: string;
  replyCount?: number;
  onRetry?: (clientId: string) => void;
  /** Shown next to the reply count on a thread root, e.g. the agent status chip. */
  threadAccessory?: ReactNode;
}

function SystemLine({ message, directory }: Pick<MessageItemProps, "message" | "directory">) {
  const date = new Date(message.createdAtMs);
  return (
    <li
      data-testid={CHAT_TEST_IDS.message}
      data-message-id={message.id ?? undefined}
      data-author-kind="system"
      className="flex gap-3 px-4 py-1"
    >
      <div className="flex w-7 shrink-0 justify-center pt-px">
        <InfoIcon className="size-4 text-muted-foreground" aria-hidden="true" />
      </div>
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
        <span className="sr-only">Systemmeldung:</span>
        <MessageBody
          body={message.body}
          directory={directory}
          className="min-w-0 text-xs text-muted-foreground"
        />
        <time
          dateTime={date.toISOString()}
          title={fullFormat.format(date)}
          className="text-xs text-muted-foreground tabular-nums"
        >
          {formatMessageTime(message.createdAtMs)}
        </time>
      </div>
    </li>
  );
}

export function MessageItem({
  message,
  directory,
  compact = false,
  threadHref,
  replyCount = 0,
  onRetry,
  threadAccessory,
}: MessageItemProps) {
  if (message.authorKind === "system") {
    return <SystemLine message={message} directory={directory} />;
  }
  return (
    <MessageRow
      message={message}
      directory={directory}
      compact={compact}
      threadHref={threadHref}
      replyCount={replyCount}
      onRetry={onRetry}
      threadAccessory={threadAccessory}
    />
  );
}

function MessageRow({
  message,
  directory,
  compact = false,
  threadHref,
  replyCount = 0,
  onRetry,
  threadAccessory,
}: MessageItemProps) {
  const author = principalLabel(message.authorId, directory);
  const agent = isAgentKind(message.authorKind);
  const time = formatMessageTime(message.createdAtMs);
  const dateTime = new Date(message.createdAtMs).toISOString();
  const fullTime = fullFormat.format(new Date(message.createdAtMs));
  const failed = message.status === "failed";
  const showReplies = Boolean(threadHref) && replyCount > 0;

  return (
    <li
      data-testid={CHAT_TEST_IDS.message}
      data-message-id={message.id ?? undefined}
      data-client-id={message.clientId ?? undefined}
      data-status={message.status}
      aria-busy={message.status === "sending" || undefined}
      className={cn(
        "group/message relative flex gap-3 px-4 hover:bg-muted/50 focus-within:bg-muted/50",
        compact ? "py-0.5" : "pt-2 pb-0.5",
        // Phone: the 44px reply control must not reach into the next row.
        threadHref && "max-md:min-h-11",
      )}
    >
      <div className="flex w-7 shrink-0 justify-center">
        {compact ? (
          <time
            dateTime={dateTime}
            title={fullTime}
            className="invisible pt-0.5 text-xs text-muted-foreground tabular-nums group-hover/message:visible group-focus-within/message:visible"
          >
            {timeFormat.format(new Date(message.createdAtMs))}
          </time>
        ) : (
          <MessageAvatar label={author} agent={agent} />
        )}
      </div>
      <div className={cn("min-w-0 flex-1", threadHref && "max-md:pr-10")}>
        {compact ? (
          <span className="sr-only">{author}</span>
        ) : (
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            <span className="min-w-0 text-sm font-medium break-words">{author}</span>
            {message.authorKind === "agent_account" ? (
              <AgentBotBadge />
            ) : agent ? (
              <span className="text-xs text-muted-foreground">Agent</span>
            ) : null}
            <time
              dateTime={dateTime}
              title={fullTime}
              className="text-xs text-muted-foreground tabular-nums"
            >
              {time}
            </time>
          </div>
        )}
        {agent ? (
          <div className="min-w-0 text-sm text-pretty">
            <MessageResponse>{message.body}</MessageResponse>
          </div>
        ) : (
          <MessageBody
            body={message.body}
            directory={directory}
            className={message.status === "sent" ? undefined : "text-muted-foreground"}
          />
        )}
        {failed ? (
          <div
            role="alert"
            data-testid={CHAT_TEST_IDS.sendFailed}
            className="flex flex-wrap items-center gap-x-1 text-xs text-destructive"
          >
            <CircleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
            <span>nicht gesendet</span>
            {message.rateLimited ? (
              <span
                data-testid={CHAT_TEST_IDS.sendRateLimited}
                data-retry-after={message.rateLimited.retryAfterSeconds ?? undefined}
                className="min-w-0 break-words tabular-nums"
              >
                <span aria-hidden="true">· </span>
                {rateLimitHint(message.rateLimited.retryAfterSeconds)}
              </span>
            ) : null}
            {onRetry && message.clientId ? (
              <Button
                type="button"
                variant="link"
                size="xs"
                componentId="chat.message.retry"
                data-testid={CHAT_TEST_IDS.sendRetry}
                className="min-h-11 px-1 text-xs text-destructive md:min-h-0"
                onClick={() => onRetry(message.clientId as string)}
              >
                erneut senden
              </Button>
            ) : null}
          </div>
        ) : null}
        {showReplies || threadAccessory ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2">
            {showReplies && threadHref ? (
              <Link
                to={threadHref}
                componentId="chat.message.replies"
                data-testid={CHAT_TEST_IDS.replyCount}
                className="inline-flex min-h-11 items-center gap-1 rounded-sm text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring md:min-h-0 md:py-0.5"
              >
                <MessageSquareIcon className="size-4 shrink-0" aria-hidden="true" />
                <span className="tabular-nums">{replyCountLabel(replyCount)}</span>
              </Link>
            ) : null}
            {threadAccessory}
          </div>
        ) : null}
      </div>
      {threadHref && message.status === "sent" ? (
        <Link
          to={threadHref}
          componentId="chat.message.reply"
          aria-label={`Im Thread antworten: Nachricht von ${author}`}
          title="Im Thread antworten"
          data-testid={CHAT_TEST_IDS.replyAction}
          className="absolute top-0 right-1 flex size-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-ring md:top-1 md:right-3 md:size-7 md:border md:border-border md:bg-background md:opacity-0 md:group-hover/message:opacity-100 md:group-focus-within/message:opacity-100"
        >
          <MessageSquareIcon className="size-4" aria-hidden="true" />
        </Link>
      ) : null}
    </li>
  );
}
