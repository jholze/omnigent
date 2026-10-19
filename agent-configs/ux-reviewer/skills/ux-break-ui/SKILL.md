---
name: ux-break-ui
description: Stress a team-chat view with realistic worst-case data (long German names, unbroken URLs, 0/1/500 items, missing avatars, huge counts, emoji, RTL) through the test fixture boundary, screenshot it at real widths and report what broke with signature, cause and fix. Read-only for the reviewer.
---

<!-- Adapted for Omnigent from emilkowalski/skills break-ui (+CATALOG.md) (MIT,
(c) 2026 Emil Kowalski). The reviewer variant never edits code; see docs/ux/NOTICES.md. -->

# Break the UI with worst-case data

Demo data is chosen to look good. Undo that, one field at a time, with values a real user
could produce or the actual schema limit. Never nonsense like 5,000 "a"s.

## Rules
- Plausible or schema-backed (Pydantic/SQLAlchemy column, API type, `maxLength`). No limit
  found → finding "unbounded".
- Change the data, not the component: use the existing test seams (Vitest props/mocks,
  `tests/e2e_ui` fixtures, API stubs). The reviewer only uses seams that need no repo edit
  (e.g. posting messages through the test server/API in the test process, Playwright
  `browser_evaluate` on a test page); otherwise describe the fixture in a design plan.
- One dataset hits many failures, spread across the first visible rows.

## Worst-case values for the team chat
- Names: "Aleksandra Wiśniewska-Kowalczyk", "Jo", "Dr. Maximilian von Hohenzollern-Sigmaringen III",
  emoji-first display name, agent name `coder_deepseek-review-2`.
- Text: unbroken URL (120+ chars), email `bartholomew.fitzgerald@northwind-industries-holdings.example.com`,
  50-line paste, single character, only emoji, Arabic/Hebrew snippet (RTL), Thai/Vietnamese
  diacritics, code fence without language, mention chip with long name.
- Counts: unread 0 / 1 / 99 / 1,284 ("1 Nachricht" vs "1 Nachrichten"), 500 messages in a channel,
  12 tool calls in one agent turn, 30 TaskSteps, approval with a 2,000-char payload.
- Time: just now, yesterday 23:59, 400 days ago; durations 0.2 s and 3 h 12 min (tabular).
- States: missing avatar, broken image, agent status every FR-044 value, failed send,
  reconnect gap (FR-082), read-only approval for viewer < EDIT (FR-056).
- Environment: 320 px, 200 % zoom, dark mode, reduced motion.

## Failure signatures → cause → fix
Squished avatar → flex shrink → `shrink-0` · text overflows → `min-width:auto` → `min-w-0` ·
URL past edge → no break opportunity → `overflow-wrap:anywhere` · trailing action off-screen →
middle took the space → `min-w-0` middle, `shrink-0` action · badge wraps → `whitespace-nowrap
shrink-0` · wrong initials → naive split → `Intl.Segmenter`, first+last word, fallback icon ·
"1 Nachrichten" → hardcoded plural → `Intl.PluralRules` · jittering numbers → proportional
figures → `tabular-nums` · `1284` / `NaN` → raw number → `Intl.NumberFormat`, null guard ·
truncated with no way to read → add `title`/tooltip/detail · 500 rows stutter → virtualise ·
raw `<b>`/`**x**` in human message → wrong layer; human text stays text (FR-020).
Truncate vs wrap per field: wrap names in detail views; end-truncate previews; middle-truncate
file names/paths/hashes; never truncate numbers, dates, amounts.

## Report
| # | Severity (Broken / Ugly / Fragile) | Field | Worst-case value | What happens (+png) | Fix (file:line) |
Then "Entscheidungen für Jens" (truncate vs wrap etc., one line each with recommendation) and
"Was gehalten hat". Map Broken → Blocker/High, Ugly → Medium, Fragile → Medium/Nit in the
design review.
