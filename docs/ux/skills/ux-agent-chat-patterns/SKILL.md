---
name: ux-agent-chat-patterns
description: Agent-native chat patterns for Omnigent's team chat - conversation/message/tool/task/reasoning/confirmation building blocks (AI Elements vocabulary, prompt-kit context), mapped to the existing web/src/components/ai-elements code and the spec (FR-044..058, FR-078..090). Load when building or reviewing message lists, agent threads, tool calls, steps, approvals or the composer.
---

<!-- Adapted for Omnigent from vercel/ai-elements skills/ai-elements (Apache-2.0,
Copyright 2023 Vercel, Inc.) and ibelick/prompt-kit llms.txt (MIT, (c) 2025 Julien
Thibeaut). Modified 2026-10-18: Next.js, AI SDK, AI Gateway and CLI-install parts removed;
component vocabulary mapped to Omnigent. See docs/ux/NOTICES.md. -->

# Agent-native chat patterns

## Ground rules for this repo
- AI Elements is a shadcn-style registry: components live **in our code**
  (`web/src/components/ai-elements/`: conversation, message, reasoning, shimmer,
  code-block, streamdown-security). We extend those files' patterns; we do **not** run
  `npx ai-elements`, `shadcn add`, or add the AI SDK (`useChat`, `ToolUIPart`) – Omnigent
  has its own stream (`web/src/store/conversationRegistry.ts`, session status events).
- Missing blocks (tool group, task list, prompt suggestions, TaskSteps) are built lean in
  `web/src/chat/` (ADR-0008), on `radix-ui` + lucide, with the props style of AI Elements
  (compound components, spread native attributes, `className` via `cn`).
- interior.dev TaskSteps: rebuild the behaviour, no code copy until Q-20 (license) is resolved.
- prompt-kit (https://www.prompt-kit.com, `llms.txt`) is a second vocabulary for the same
  problems: prompt-input, chat-container (smart auto-scroll), scroll-button, message with
  actions, loader, response-stream, tool, reasoning, steps, feedback bar. Use it to
  compare behaviours, not to paste code.
- Human message bodies are text nodes only (FR-020); only agent/bot Markdown goes through
  ai-elements `message` with `streamdown-security.ts`.

## Building blocks and what "good" means
| Block | Reference vocabulary | Omnigent rules |
|---|---|---|
| Conversation / message list | AI Elements `Conversation`, prompt-kit `ChatContainer` + `ScrollButton` | Stick to bottom only if the user was at the bottom; otherwise a "Neue Nachrichten" pill (FR-080). Virtualise long lists (`@tanstack/react-virtual` is in deps; NFR-006 = 500 messages). Role `log` / live region batched ≤ 1 announcement per 2 s. |
| Message | `Message`, `MessageContent`, `MessageResponse`, actions on hover | Flat rows, author + time (tabular-nums, `Intl.DateTimeFormat`), grouping of consecutive messages, hover actions also reachable by keyboard. Failed send: inline "nicht gesendet · erneut senden" with the same `client_id` (FR-084). |
| Streaming response | `MessageResponse` (streamdown), prompt-kit `ResponseStream` | Buffered release, ≤ 1 update per frame (FR-080); no layout jump at the end; code blocks reserve height. |
| Reasoning | `Reasoning` (auto-open while streaming, collapse when done), `Shimmer` | Collapsed by default after finish; shimmer only while active; `aria-expanded`. |
| Tool call | `Tool` with states input-streaming → input-available → output-available / output-error | Compact one-line header (lucide icon 16 px + tool name + status + duration), details collapsible; group consecutive tool calls ("3 Werkzeuge") so the thread stays readable. |
| Steps / plan / tasks | `Task`, `Plan`, `Queue`, `ChainOfThought`; interior.dev TaskSteps | Status pending · active · done · error (FR-089), values in `ux-motion`. Progress "n von m". Error keeps following steps pending. |
| Approval / elicitation | `Confirmation` (request / accepted / rejected) | Inline at the request position (FR-055); actionable only for EDIT+, else read-only text (FR-056); resolved elsewhere → resolved state with verdict + resolver within 2 s (FR-057); 403/already resolved → show server verdict, no error toast (FR-058). Destructive verdicts confirm or offer undo. |
| Status chip | — | Labels verbatim from FR-044 ("startet…", "arbeitet", "braucht Antwort", "fertig", "Fehler", "entlassen"); icon + text, never colour alone; crossfade 200 ms; fixed width to avoid jitter. "braucht Antwort" is the one accent in the channel. |
| Composer | `PromptInput`, prompt-kit `PromptInput` | Same card look and send key as `ChatComposer.tsx` / `isComposerSendKey` (NFR-011); never block paste; stays usable while sending; attachments/actions as 20 px toolbar icons with `aria-label`. |
| Suggestions | `Suggestion` | Up to 3 prompts in empty threads/channels, insert into composer on click (FR-081). |
| Empty / loading / error | `Shimmer`, skeletons | Skeleton rows matching final layout; error text with a next step; empty state with one action. |

## Agent-native details that make it feel right
- Show who acts: human vs. agent avatar treatment and an "Agent" marker; agent identity
  never looks like a human.
- Make waiting legible: what the agent is doing now (active step label), how long it has
  run, and whether it needs the user ("braucht Antwort" + count of pending approvals).
- Never surprise-scroll the reader; never steal focus from the composer when new agent
  output arrives.
- Long outputs collapse with a clear "mehr anzeigen"; copy buttons on code and results.
- Keyboard: Enter/shortcut per `isComposerSendKey`, Escape closes the thread pane, arrow
  navigation where lists are interactive. Keyboard actions are never animated.
