import { CHAT_THREAD_LABEL_KEY } from "@/chat/agent/agentThreadStore";
import type { Conversation } from "@/hooks/useConversations";

/** Infer ownership independently of the server's visibility query parameter. */
export function sessionVisibility(
  row: Pick<Conversation, "owner" | "permission_level">,
  viewerId: string | null,
): "mine" | "shared" {
  // Admin permissions do not change who owns a session.
  if (viewerId !== null && row.owner) return row.owner === viewerId ? "mine" : "shared";
  if (row.permission_level != null && row.permission_level > 0 && row.permission_level < 4)
    return "shared";
  // Older single-user servers omit ownership; unknown rows must not appear as shared.
  return "mine";
}

export function filterSessionScope(
  rows: Conversation[],
  scope: "mine" | "shared",
  viewerId: string | null,
): Conversation[] {
  return rows.filter((row) => !row.archived && sessionVisibility(row, viewerId) === scope);
}

/** True for a session a team-chat Agent-Thread started (label `omnigent.chat.v1`). */
export function isAgentThreadSession(row: Pick<Conversation, "labels">): boolean {
  const labels = row.labels as Record<string, string> | null | undefined;
  return labels != null && labels[CHAT_THREAD_LABEL_KEY] !== undefined;
}

/**
 * Whether the main session sidebar shows this row (FR-085).
 *
 * An Agent-Thread session is listed only for its owner (AC-085b). Everyone
 * else with a grant reaches it from the owning Thread instead (AC-085a).
 */
export function showsInMainSidebar(
  row: Pick<Conversation, "labels" | "owner" | "permission_level">,
  viewerId: string | null,
): boolean {
  return !isAgentThreadSession(row) || sessionVisibility(row, viewerId) === "mine";
}
