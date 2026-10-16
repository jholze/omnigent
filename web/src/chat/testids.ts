// Shared `data-testid` values for the team chat UI. Playwright reads this file
// too (tests/e2e_ui/team_chat/conftest.py), so keep one `key: "value"` per line.
export const CHAT_TEST_IDS = {
  navEntry: "chat-nav-entry",
  page: "chat-page",
  sidebar: "chat-sidebar",
  message: "chat-message",
  composer: "chat-composer",
  threadPane: "thread-pane",
  agentStatusChip: "agent-status-chip",
  approvalCard: "approval-card",
} as const;

export type ChatTestId = (typeof CHAT_TEST_IDS)[keyof typeof CHAT_TEST_IDS];
