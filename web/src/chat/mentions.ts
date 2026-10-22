/**
 * Mention autocomplete for the team-chat composer.
 *
 * Candidates come from data already in the client: channel members, the
 * bootstrap directory, and the Omnigent agent handle. A picked candidate is
 * shown as `@Label` in the draft and serialized to its `<@kind:id>` token on
 * send; the rest of the draft stays literal.
 */
import type { ChannelMember, DirectoryEntry } from "./chatApi";
import { principalLabel } from "./MessageItem";

export type MentionKind = "user" | "agent_account" | "omnigent_agent";

/** Handle that starts an agent thread (`chat_agent_threads._HANDLE`). */
export const OMNIGENT_AGENT_HANDLE = "agent";

export interface MentionCandidate {
  kind: MentionKind;
  /** Principal id, or the handle for an Omnigent agent. */
  id: string;
  /** Name shown in the list and inserted after `@`. */
  label: string;
  /** Owner's name, for agent accounts. */
  owner: string | null;
  handle: string | null;
  /** `aktiv` or `inaktiv` for an agent account that is a channel member. */
  status: "aktiv" | "inaktiv" | null;
}

/** A picked mention in the draft. */
export interface MentionChip {
  key: string;
  kind: MentionKind;
  label: string;
  token: string;
}

export function mentionToken(candidate: Pick<MentionCandidate, "kind" | "id">): string {
  return `<@${candidate.kind}:${candidate.id}>`;
}

/** Owner name: directory label, else the capitalized local part of the id. */
function ownerLabel(ownerId: string, directory: Map<string, DirectoryEntry>): string {
  const label = principalLabel(ownerId, directory);
  if (label !== ownerId) return label;
  const local = ownerId.split("@", 1)[0].trim() || ownerId;
  return local.slice(0, 1).toUpperCase() + local.slice(1);
}

function memberPresence(member: ChannelMember): "aktiv" | "inaktiv" | null {
  return member.status === "aktiv" || member.status === "inaktiv" ? member.status : null;
}

/** Every mentionable principal except *me*, one row per id. */
export function mentionCandidates(
  members: ChannelMember[],
  directory: Map<string, DirectoryEntry>,
  me: string,
): MentionCandidate[] {
  const byId = new Map<string, MentionCandidate>();
  const add = (
    id: string,
    kind: MentionKind,
    fallbackLabel: string,
    status: "aktiv" | "inaktiv" | null = null,
  ) => {
    if (id === "" || id === me || byId.has(id)) return;
    const entry = directory.get(id);
    const label = entry ? principalLabel(id, directory) : fallbackLabel.trim() || id;
    const ownerId = kind === "agent_account" ? entry?.owner_id : null;
    byId.set(id, {
      kind,
      id,
      label,
      owner: ownerId ? ownerLabel(ownerId, directory) : null,
      handle: entry?.handle?.trim() || null,
      status: kind === "agent_account" ? status : null,
    });
  };
  for (const member of members) {
    const kind: MentionKind = member.kind === "agent_account" ? "agent_account" : "user";
    // A revoked account is removed from the active directory, so a stale
    // member row must not stay in the @ list.
    if (kind === "agent_account" && directory.get(member.principal_id)?.kind !== "agent_account") {
      continue;
    }
    add(member.principal_id, kind, member.display, memberPresence(member));
  }
  for (const entry of directory.values()) {
    if (entry.kind === "user" || entry.kind === "agent_account") {
      add(entry.id, entry.kind, entry.id);
    }
  }
  const people = [...byId.values()].sort((a, b) => a.label.localeCompare(b.label, "de-DE"));
  const agent: MentionCandidate = {
    kind: "omnigent_agent",
    id: OMNIGENT_AGENT_HANDLE,
    label: OMNIGENT_AGENT_HANDLE,
    owner: null,
    handle: OMNIGENT_AGENT_HANDLE,
    status: null,
  };
  return [agent, ...people];
}

/** Case-insensitive prefix match on the name, the handle, and a user's id. */
export function filterMentionCandidates(
  candidates: MentionCandidate[],
  query: string,
): MentionCandidate[] {
  const needle = query.toLocaleLowerCase("de-DE");
  if (needle === "") return candidates;
  const starts = (value: string | null) =>
    value !== null && value.toLocaleLowerCase("de-DE").startsWith(needle);
  return candidates.filter(
    (candidate) =>
      starts(candidate.label) ||
      starts(candidate.handle) ||
      (candidate.kind === "user" && starts(candidate.id)),
  );
}

/** The `@query` being typed at *caret*, if any. */
export function findMentionTrigger(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  const match = /(?:^|\s)@([^\s@<>]*)$/.exec(text.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[1].length - 1, query: match[1] };
}

/** Start offsets of `@label` in *text* that end at a word boundary. */
function labelOffsets(text: string, label: string): number[] {
  const needle = `@${label}`;
  const offsets: number[] = [];
  for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + 1)) {
    const after = text.charAt(at + needle.length);
    if (after === "" || !/[\p{L}\p{N}_]/u.test(after)) offsets.push(at);
  }
  return offsets;
}

/** Longest labels claim their text first, so `@Jens` never eats `@Jens Weber`. */
function longestFirst(chips: MentionChip[]): MentionChip[] {
  return [...chips].sort((a, b) => b.label.length - a.label.length);
}

/** Chips whose `@label` is still in the draft. */
export function presentChips(text: string, chips: MentionChip[]): MentionChip[] {
  const used = new Set<number>();
  const kept = new Set(
    longestFirst(chips).filter((chip) => {
      const at = labelOffsets(text, chip.label).find((offset) => !used.has(offset));
      if (at === undefined) return false;
      used.add(at);
      return true;
    }),
  );
  return chips.filter((chip) => kept.has(chip));
}

/** Draft without one chip's `@label` (and the space after it). */
export function removeChipText(text: string, chip: MentionChip): string {
  const [at] = labelOffsets(text, chip.label);
  if (at === undefined) return text;
  let end = at + chip.label.length + 1;
  if (text.charAt(end) === " ") end += 1;
  return text.slice(0, at) + text.slice(end);
}

/** Replace each chip's `@label` with its token; everything else stays literal. */
export function serializeMentions(text: string, chips: MentionChip[]): string {
  const used = new Set<number>();
  const edits: { at: number; length: number; token: string }[] = [];
  for (const chip of longestFirst(chips)) {
    const at = labelOffsets(text, chip.label).find(
      (offset) =>
        !used.has(offset) &&
        !edits.some((edit) => offset > edit.at && offset < edit.at + edit.length),
    );
    if (at === undefined) continue;
    used.add(at);
    edits.push({ at, length: chip.label.length + 1, token: chip.token });
  }
  edits.sort((a, b) => a.at - b.at);
  let out = "";
  let cursor = 0;
  for (const edit of edits) {
    if (edit.at < cursor) continue;
    out += text.slice(cursor, edit.at) + edit.token;
    cursor = edit.at + edit.length;
  }
  return out + text.slice(cursor);
}
