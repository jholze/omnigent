/**
 * Team-chat composer: the agent composer's card and send key (NFR-011).
 *
 * Enter sends unless the user chose Mod+Enter; Shift+Enter is a newline.
 * Touch and phone viewports send with the button only (`isComposerSendKey`).
 * The body is trimmed; a blank body is never posted (AC-015a). The composer
 * stays enabled while a send is in flight or after one failed (FR-084).
 *
 * Typing `@` opens a mention list (FR-026). While it is open, the arrow keys
 * move the highlight, Enter picks and Escape closes; a pick adds a chip that
 * is sent as its `<@kind:id>` token.
 */
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BotIcon, XIcon } from "lucide-react";

import {
  ChatComposer,
  ComposerChipRow,
  ComposerSendButton,
} from "@/components/composer/ChatComposer";
import { useIsCoarsePointer } from "@/hooks/useIsCoarsePointer";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { readSubmitWithModEnter } from "@/lib/composerSendShortcutPreferences";
import { cn } from "@/lib/utils";
import { fetchChannelMembers, teamChatQueryKeys, type DirectoryEntry } from "./chatApi";
import { sendChatMessage } from "./chatOutbox";
import {
  filterMentionCandidates,
  findMentionTrigger,
  mentionCandidates,
  mentionToken,
  presentChips,
  removeChipText,
  serializeMentions,
  type MentionCandidate,
  type MentionChip,
} from "./mentions";
import { CHAT_TEST_IDS } from "./testids";

export interface ChatComposerCardProps {
  channelId: string;
  threadRootId?: string | null;
  me: string;
  placeholder: string;
  /** Accessible name of the text field. */
  label: string;
  /** Bootstrap directory, for mention autocomplete. */
  directory?: Map<string, DirectoryEntry>;
  className?: string;
}

const EMPTY_DIRECTORY = new Map<string, DirectoryEntry>();

export function ChatComposerCard({
  channelId,
  threadRootId = null,
  me,
  placeholder,
  label,
  directory = EMPTY_DIRECTORY,
  className,
}: ChatComposerCardProps) {
  const queryClient = useQueryClient();
  const isMobileViewport = useIsMobileViewport();
  const isCoarsePointer = useIsCoarsePointer();
  const preventsKeyboardSubmit = isMobileViewport || isCoarsePointer;
  const [submitWithModEnter] = useState(() => readSubmitWithModEnter());
  const [text, setText] = useState("");
  const [chips, setChips] = useState<MentionChip[]>([]);
  const [trigger, setTrigger] = useState<{ start: number; query: string } | null>(null);
  const [dismissedStart, setDismissedStart] = useState<number | null>(null);
  const [highlight, setHighlight] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pendingCaretRef = useRef<number | null>(null);
  const columnRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const canSend = text.trim() !== "";

  // Loaded once per channel on mount; the list filters it locally.
  const members = useQuery({
    queryKey: teamChatQueryKeys.members(channelId),
    queryFn: ({ signal }) => fetchChannelMembers(channelId, signal),
    staleTime: Infinity,
    retry: false,
  });
  const candidates = useMemo(
    () => mentionCandidates(members.data ?? [], directory, me),
    [members.data, directory, me],
  );
  const matches = useMemo(
    () => (trigger ? filterMentionCandidates(candidates, trigger.query) : []),
    [candidates, trigger],
  );
  const open = trigger !== null;
  const active = Math.min(highlight, Math.max(matches.length - 1, 0));
  const optionId = (index: number) => `${listId}-option-${index}`;

  useLayoutEffect(() => {
    const caret = pendingCaretRef.current;
    const textarea = textareaRef.current;
    if (caret === null || !textarea) return;
    pendingCaretRef.current = null;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  }, [text]);

  useEffect(() => {
    if (!open) return;
    document.getElementById(`${listId}-option-${active}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active, listId]);

  // A press outside the composer closes the list; a tap on a row does not.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node) || !columnRef.current?.contains(event.target)) {
        setTrigger(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const updateTrigger = (value: string, caret: number) => {
    const next = findMentionTrigger(value, caret);
    if (next === null || next.start !== dismissedStart) setDismissedStart(null);
    const visible =
      next !== null &&
      next.start !== dismissedStart &&
      !chips.some((chip) => chip.label === next.query);
    setTrigger(visible ? next : null);
    if (!visible || next.query !== trigger?.query) setHighlight(0);
  };

  const pick = (candidate: MentionCandidate) => {
    if (!trigger) return;
    const end = trigger.start + 1 + trigger.query.length;
    const inserted = `@${candidate.label} `;
    pendingCaretRef.current = trigger.start + inserted.length;
    setText(text.slice(0, trigger.start) + inserted + text.slice(end));
    setChips((current) => [
      ...current,
      {
        key: `${candidate.kind}:${candidate.id}:${trigger.start}:${current.length}`,
        kind: candidate.kind,
        label: candidate.label,
        token: mentionToken(candidate),
      },
    ]);
    setTrigger(null);
    setHighlight(0);
  };

  const dismiss = () => {
    if (trigger) setDismissedStart(trigger.start);
    setTrigger(null);
  };

  const submit = () => {
    const sent = sendChatMessage(queryClient, {
      channelId,
      threadRootId,
      authorId: me,
      body: serializeMentions(text, chips),
    });
    if (!sent) return;
    setText("");
    setChips([]);
    setTrigger(null);
    setDismissedStart(null);
    textareaRef.current?.focus();
  };

  const removeChip = (chip: MentionChip) => {
    pendingCaretRef.current = null;
    setText((current) => removeChipText(current, chip));
    setChips((current) => current.filter((item) => item.key !== chip.key));
    textareaRef.current?.focus();
  };

  return (
    <div
      className={cn(
        "shrink-0 px-3 pt-1 pb-[max(20px,env(safe-area-inset-bottom))] md:px-4 md:pb-4",
        className,
      )}
    >
      <div ref={columnRef} className="relative">
        {open ? (
          <MentionList
            id={listId}
            matches={matches}
            active={active}
            optionId={optionId}
            onHover={setHighlight}
            onPick={pick}
          />
        ) : null}
        <ChatComposer
          data-testid={CHAT_TEST_IDS.composer}
          keyboard={{ submitWithModEnter, preventsKeyboardSubmit }}
          input={{
            ref: textareaRef,
            value: text,
            rows: 1,
            placeholder,
            "aria-label": label,
            "aria-autocomplete": "list",
            "aria-controls": open ? listId : undefined,
            "aria-activedescendant": open && matches.length > 0 ? optionId(active) : undefined,
            "data-testid": CHAT_TEST_IDS.composerInput,
            onChange: (event) => {
              const value = event.target.value;
              setText(value);
              setChips((current) => presentChips(value, current));
              updateTrigger(value, event.target.selectionStart ?? value.length);
            },
            onSelect: (event) => {
              const textarea = event.currentTarget;
              if (textarea.selectionStart !== textarea.selectionEnd) {
                if (open) setTrigger(null);
                return;
              }
              const next = findMentionTrigger(textarea.value, textarea.selectionStart);
              if (next?.start !== trigger?.start || next?.query !== trigger?.query) {
                updateTrigger(textarea.value, textarea.selectionStart);
              }
            },
            onKeyDown: (event, intent) => {
              if (open && !event.nativeEvent.isComposing) {
                const plain = !event.shiftKey && !event.altKey && !event.metaKey && !event.ctrlKey;
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  if (matches.length === 0) return;
                  const step = event.key === "ArrowDown" ? 1 : -1;
                  setHighlight((active + step + matches.length) % matches.length);
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  dismiss();
                  return;
                }
                if (event.key === "Enter" && plain) {
                  event.preventDefault();
                  const candidate = matches[active];
                  if (candidate) pick(candidate);
                  else dismiss();
                  return;
                }
              }
              if (intent.shouldSubmitFromKeyboard) {
                event.preventDefault();
                submit();
              }
            },
          }}
          slots={{
            attachments:
              chips.length > 0 ? (
                <ComposerChipRow className="gap-1.5">
                  {chips.map((chip) => (
                    <span
                      key={chip.key}
                      data-testid={CHAT_TEST_IDS.composerMentionChip}
                      data-mention-token={chip.token}
                      className="inline-flex max-w-full items-center gap-1 rounded-md bg-muted py-0.5 pr-0.5 pl-1.5 text-xs font-medium text-foreground"
                    >
                      {chip.kind === "agent_account" ? (
                        <BotIcon className="size-3.5 shrink-0" aria-hidden="true" />
                      ) : null}
                      <span className="truncate">@{chip.label}</span>
                      <button
                        type="button"
                        aria-label={`Erwähnung @${chip.label} entfernen`}
                        className="inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring max-md:size-7"
                        onClick={() => removeChip(chip)}
                      >
                        <XIcon className="size-3" aria-hidden="true" />
                      </button>
                    </span>
                  ))}
                </ComposerChipRow>
              ) : null,
          }}
          actions={{
            leading: null,
            trailing: (
              <ComposerSendButton
                type="button"
                label="Senden"
                componentId="chat.composer.send"
                disabled={!canSend}
                className="max-md:size-11"
                onClick={submit}
              />
            ),
          }}
        />
      </div>
    </div>
  );
}

function MentionList({
  id,
  matches,
  active,
  optionId,
  onHover,
  onPick,
}: {
  id: string;
  matches: MentionCandidate[];
  active: number;
  optionId: (index: number) => string;
  onHover: (index: number) => void;
  onPick: (candidate: MentionCandidate) => void;
}) {
  return (
    <div
      data-testid={CHAT_TEST_IDS.mentionList}
      className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-border bg-popover text-foreground shadow-md"
    >
      {matches.length === 0 ? (
        <p
          data-testid={CHAT_TEST_IDS.mentionEmpty}
          role="status"
          className="px-3 py-3 text-sm text-foreground"
        >
          Niemand gefunden
        </p>
      ) : (
        <ul
          id={id}
          role="listbox"
          aria-label="Erwähnen"
          className="flex max-h-[min(18rem,40dvh)] flex-col overflow-y-auto overscroll-contain py-1"
        >
          {matches.map((candidate, index) => (
            <li
              key={`${candidate.kind}:${candidate.id}`}
              id={optionId(index)}
              role="option"
              aria-selected={index === active}
              data-testid={CHAT_TEST_IDS.mentionOption}
              data-mention-kind={candidate.kind}
              data-mention-id={candidate.id}
              className={cn(
                "group flex min-h-11 shrink-0 cursor-pointer items-center gap-2 px-3 py-2 text-sm text-foreground md:min-h-9 md:py-1.5",
                index === active && "bg-muted",
              )}
              // Keep focus (and the caret) in the textarea.
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => onHover(index)}
              onClick={() => onPick(candidate)}
            >
              <MentionOptionLabel candidate={candidate} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MentionOptionLabel({ candidate }: { candidate: MentionCandidate }) {
  if (candidate.kind === "omnigent_agent") {
    return (
      <>
        <span className="min-w-0 flex-1 truncate font-medium">@{candidate.label}</span>
        <span className="shrink-0 text-xs text-muted-foreground group-aria-selected:text-foreground">
          Omnigent-Agent
        </span>
      </>
    );
  }
  if (candidate.kind === "agent_account") {
    return (
      <>
        <span className="min-w-0 flex-1 truncate">
          {candidate.owner ? `${candidate.label} (${candidate.owner})` : candidate.label}
        </span>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-border px-1 text-xs text-foreground">
          <BotIcon className="size-3" aria-hidden="true" />
          Bot
        </span>
      </>
    );
  }
  return <span className="min-w-0 flex-1 truncate">{candidate.label}</span>;
}
