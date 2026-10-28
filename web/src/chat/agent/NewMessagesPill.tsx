/**
 * "Neue Nachrichten" pill (FR-080, AC-080a).
 *
 * Shown over the message list when streamed agent output grows while the
 * reader has scrolled up. A click jumps to the bottom; focus stays where the
 * browser puts it, never on the composer, and the pill is never autofocused
 * (INT-07). Static on purpose: no enter motion in this task.
 */
import { ArrowDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { CHAT_TEST_IDS } from "../testids";

export const NEW_MESSAGES_LABEL = "Neue Nachrichten";

export function NewMessagesPill({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-4">
      <Button
        type="button"
        variant="outline"
        size="sm"
        componentId="chat.new_messages"
        data-testid={CHAT_TEST_IDS.newMessages}
        className="pointer-events-auto h-auto min-h-11 max-w-full rounded-full px-3 text-xs whitespace-normal shadow-sm"
        onClick={onClick}
      >
        <ArrowDownIcon className="size-3.5" aria-hidden="true" />
        <span className="min-w-0 break-words">{NEW_MESSAGES_LABEL}</span>
      </Button>
    </div>
  );
}
