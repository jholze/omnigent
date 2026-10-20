// Shared `data-testid` values for the team chat UI. Playwright reads this file
// too (tests/e2e_ui/team_chat/conftest.py), so keep one `key: "value"` per line.
export const CHAT_TEST_IDS = {
  navEntry: "chat-nav-entry",
  page: "chat-page",
  sidebar: "chat-sidebar",
  back: "chat-back",
  bootstrapError: "chat-bootstrap-error",
  bootstrapRetry: "chat-bootstrap-retry",
  channelMissing: "chat-channel-missing",
  message: "chat-message",
  composer: "chat-composer",
  threadPane: "thread-pane",
  composerInput: "chat-composer-input",
  messageList: "chat-message-list",
  messagesEmpty: "chat-messages-empty",
  messagesError: "chat-messages-error",
  messagesRetry: "chat-messages-retry",
  sendFailed: "chat-send-failed",
  sendRetry: "chat-send-retry",
  replyCount: "chat-reply-count",
  replyAction: "chat-reply-action",
  threadBack: "chat-thread-back",
  threadClose: "chat-thread-close",
  newDm: "chat-new-dm",
  newDmDialog: "chat-new-dm-dialog",
  newDmPerson: "chat-new-dm-person",
  newDmError: "chat-new-dm-error",
  mentionChip: "chat-mention-chip",
  agentStatusChip: "agent-status-chip",
  approvalCard: "approval-card",
} as const;

export type ChatTestId = (typeof CHAT_TEST_IDS)[keyof typeof CHAT_TEST_IDS];
