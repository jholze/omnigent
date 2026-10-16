/** A `session.human_chat` signal. The text is not on the event. */

export interface HumanChatSignal {
  conversationId: string;
  id: string;
  createdAt: number;
}

type Listener = (signal: HumanChatSignal) => void;

const listeners = new Set<Listener>();

/** Tell open session-chat columns that a line was stored. */
export function emitHumanChat(signal: HumanChatSignal): void {
  for (const listener of [...listeners]) {
    listener(signal);
  }
}

/** Subscribe until the returned function is called. */
export function subscribeHumanChat(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
