/**
 * Session grant of the viewer on an Agent-Thread session, and the
 * "Session öffnen" link that depends on it (FR-052, AC-052b). Shared by the
 * thread header, the live view and the run-stuck banner.
 */
import { ArrowUpRightIcon } from "lucide-react";

import { useSession } from "@/hooks/useSession";
import { LEVEL_EDIT } from "@/lib/permissionsApi";
import { Link } from "@/lib/routing";
import { ApiError } from "@/lib/sessionsApi";
import { CHAT_TEST_IDS } from "../testids";

export type SessionAccess = "loading" | "none" | "read" | "edit" | "error";

/** READ / EDIT from the session snapshot; 403 or 404 means no grant. */
export function sessionAccessOf(
  session: { permissionLevel: number | null } | null,
  isLoading: boolean,
  error: Error | null,
): SessionAccess {
  if (session !== null) {
    const level = session.permissionLevel;
    // `null`: permissions are off on this server, everyone may act.
    if (level === null || level >= LEVEL_EDIT) return "edit";
    return level >= 1 ? "read" : "none";
  }
  if (error instanceof ApiError && (error.status === 403 || error.status === 404)) return "none";
  if (error !== null) return "error";
  return isLoading ? "loading" : "error";
}

/**
 * "Session öffnen" in the Thread header (FR-085, AC-085a). The main sidebar
 * lists an Agent-Thread session for its owner only; everyone else with a
 * grant opens it here. Without a grant the link does not render: the server
 * would refuse the session anyway.
 */
export function OpenAgentSessionLink({
  sessionId,
  testId = CHAT_TEST_IDS.threadOpenSession,
}: {
  sessionId: string;
  /** The run-stuck banner shows a second link with its own test id. */
  testId?: string;
}) {
  const { session, isLoading, error } = useSession(sessionId);
  const access = sessionAccessOf(session, isLoading, error);
  if (access !== "read" && access !== "edit") return null;
  return (
    <Link
      to={`/c/${encodeURIComponent(sessionId)}`}
      componentId="chat.thread.open_session"
      data-testid={testId}
      className="relative inline-flex min-h-11 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-[''] hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring md:min-h-8"
    >
      Session öffnen
      <ArrowUpRightIcon className="size-3.5 shrink-0" aria-hidden="true" />
    </Link>
  );
}
