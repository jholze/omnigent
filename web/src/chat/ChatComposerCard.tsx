/**
 * Team-chat composer: the agent composer's card and send key (NFR-011).
 *
 * Enter sends unless the user chose Mod+Enter; Shift+Enter is a newline.
 * Touch and phone viewports send with the button only (`isComposerSendKey`).
 * The body is trimmed; a blank body is never posted (AC-015a). The composer
 * stays enabled while a send is in flight or after one failed (FR-084).
 */
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ChatComposer, ComposerSendButton } from "@/components/composer/ChatComposer";
import { useIsCoarsePointer } from "@/hooks/useIsCoarsePointer";
import { useIsMobileViewport } from "@/hooks/useIsMobileViewport";
import { readSubmitWithModEnter } from "@/lib/composerSendShortcutPreferences";
import { cn } from "@/lib/utils";
import { sendChatMessage } from "./chatOutbox";
import { CHAT_TEST_IDS } from "./testids";

export interface ChatComposerCardProps {
  channelId: string;
  threadRootId?: string | null;
  me: string;
  placeholder: string;
  /** Accessible name of the text field. */
  label: string;
  className?: string;
}

export function ChatComposerCard({
  channelId,
  threadRootId = null,
  me,
  placeholder,
  label,
  className,
}: ChatComposerCardProps) {
  const queryClient = useQueryClient();
  const isMobileViewport = useIsMobileViewport();
  const isCoarsePointer = useIsCoarsePointer();
  const preventsKeyboardSubmit = isMobileViewport || isCoarsePointer;
  const [submitWithModEnter] = useState(() => readSubmitWithModEnter());
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const canSend = text.trim() !== "";

  const submit = () => {
    const sent = sendChatMessage(queryClient, {
      channelId,
      threadRootId,
      authorId: me,
      body: text,
    });
    if (!sent) return;
    setText("");
    textareaRef.current?.focus();
  };

  return (
    <div
      className={cn(
        "shrink-0 px-3 pt-1 pb-[max(20px,env(safe-area-inset-bottom))] md:px-4 md:pb-4",
        className,
      )}
    >
      <ChatComposer
        data-testid={CHAT_TEST_IDS.composer}
        keyboard={{ submitWithModEnter, preventsKeyboardSubmit }}
        input={{
          ref: textareaRef,
          value: text,
          rows: 1,
          placeholder,
          "aria-label": label,
          "data-testid": CHAT_TEST_IDS.composerInput,
          onChange: (event) => setText(event.target.value),
          onKeyDown: (event, intent) => {
            if (intent.shouldSubmitFromKeyboard) {
              event.preventDefault();
              submit();
            }
          },
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
  );
}
