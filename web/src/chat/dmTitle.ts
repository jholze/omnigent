/**
 * Names for a direct message the caller already belongs to.
 *
 * The title is the other people. The participant line includes the caller.
 * Labels come from the bootstrap directory, then the principal id.
 */
import type { DirectoryEntry } from "./chatApi";
import { principalLabel } from "./MessageItem";

export interface DmMember {
  principal_id: string;
}

export function dmHeading(
  me: string,
  members: readonly DmMember[],
  directory: Map<string, DirectoryEntry>,
): string {
  const others = members
    .map((member) => member.principal_id)
    .filter((id) => id !== "" && id !== me)
    .map((id) => principalLabel(id, directory));
  if (others.length === 0) return "Direktnachricht";
  return others.join(", ");
}

export function dmParticipantLine(
  me: string,
  members: readonly DmMember[],
  directory: Map<string, DirectoryEntry>,
): string {
  const names = members
    .map((member) => member.principal_id)
    .filter((id) => id !== "")
    .map((id) => {
      const label = principalLabel(id, directory);
      return id === me ? `${label} (du)` : label;
    });
  if (names.length === 0) return "";
  return `Teilnehmer: ${names.join(", ")}`;
}
