/**
 * The "Bot" badge used for an agent account: the mention row and the
 * message author share this markup.
 */
import { BotIcon } from "lucide-react";

export function AgentBotBadge() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-border px-1 text-xs text-foreground">
      <BotIcon className="size-3" aria-hidden="true" />
      Bot
    </span>
  );
}
