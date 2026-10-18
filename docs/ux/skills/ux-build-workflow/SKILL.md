---
name: ux-build-workflow
description: Use before and during every UI change in web/src/chat (team chat). The ux-builder's working discipline - intent first, use what exists, plan tokens, anti-default check, build, screenshot self-critique at 1440/768/375, update docs/ux/system.md.
---

<!-- Adapted for Omnigent from Dammyjay93/interface-design (MIT, (c) 2026 Damola Akinleye)
and anthropics/skills frontend-design (Apache-2.0, Anthropic, PBC). Modified 2026-10-18:
condensed, merged, landing-page content removed, rewritten for the Omnigent team chat.
See docs/ux/NOTICES.md. -->

# UX build workflow (team chat)

Product UI for a tool people use all day, not a landing page. The bar: if another agent
given the same task would produce the same generic output, it is not done. Different
because it came from this product (agents + humans in one thread), not different for its
own sake.

## 0. Read before touching code
1. The task in `specs/omnigent-chat/07-tasks.md` (Scope, **Out of scope**, ACs, Verification).
   Task scope beats everything here. "Keine Animationen (T-117)" means no motion code.
2. The FR/NFR it implements (`04-requirements.md`), `adr/0008-ux-basis-motion-ai-elements.md`,
   `05-design.md` sections it names.
3. `docs/ux/system.md` (design memory: decisions, tokens, patterns) and
   `docs/ux/chat-review-checklist.md` (what the reviewer will check you against).
4. Open plans for this task in `docs/ux/design-plans/` (`Status: open`). Each is
   self-contained; implement it or reject it with a reason in the plan's `Resolution`.

## 1. Intent first (compact, in your head or the PR body, not a monologue)
- Who: a developer between tasks checking on agents and teammates.
- Verb: the one thing this view is for (answer the approval, read the thread, send).
- Feel: calm, dense workbench. Name the focal element of the view and how it wins
  (size, weight, contrast, space). One focal point per view.

## 2. Use what exists (most common way agents degrade a codebase is hand-rolling)
- Controls: native HTML → existing `web/src/components/ui/*` on `radix-ui` → compose a
  Radix primitive → hand-roll only as last resort with the full keyboard/focus/ARIA contract.
- Chat building blocks: `web/src/components/ai-elements/*` (conversation, message,
  reasoning, shimmer, code-block) and `components/composer/ChatComposer.tsx`. Extend, don't fork.
- Styling: existing variants → extract a component on the second real reuse → semantic
  tokens (`bg-card`, `text-muted-foreground`) → one-off utilities only for one-offs.
  The same long className on many elements is a missing component.

## 3. Plan pass (before code, short)
Write a 5-line component checkpoint for each new surface:
```
Hierarchy:  focal element + lever (size/weight/contrast/space)
Tokens:     which existing colour/spacing/radius/type tokens; any NEW token → system.md first
States:     default, hover, active, focus-visible, disabled; data: loading, empty, error, overflow
Motion:     none | which token from system.md (and the task that allows it)
A11y:       landmarks, accessible names, live region, keyboard path
```
Then review the plan against the brief: does any part read like the default you would
produce for any chat app? Revise that part and say what changed. Product-UI defaults to
reject unless the spec asks for them:
- identical rounded cards with the same soft grey shadow for every message/tool call;
- tracked ALL-CAPS eyebrow labels above everything; meta joined with " · " everywhere;
- one coloured border on one side of alerts/toasts/cards;
- emoji as icons or status; gradient washes or glow as affordance; purple/multicolour gradients;
- `→` appended to every button label; tinted near-black (#0B0B0B) standing in for tokens;
- fade-and-slide on everything (motion must have a purpose, see `ux-motion`).

## 4. Build
- Follow `ux-stack-rules` (hard rules) and `ux-agent-chat-patterns` (domain patterns).
- Small, reviewable commits on your feature branch. Tests with the change (Vitest for
  widgets; `tests/e2e_ui` Python Playwright for journeys; never a second tree under `web/e2e`).
- UX writing: user's words, not system words ("Agent braucht Antwort", not
  "elicitation pending"). Active voice, specific button labels, the same verb through the
  flow ("Senden" → "Gesendet"). Errors say what happened and what to do; empty states
  give exactly one next action. Spec strings (FR-044 chip labels etc.) are verbatim.

## 5. Screenshot self-critique (before you call it done)
Use the Playwright MCP tools (`browser_navigate`, `browser_resize`,
`browser_take_screenshot`, `browser_snapshot`, `browser_emulate_media`,
`browser_console_messages`). Absolute filenames only:
`/tmp/omnigent-chat-ux/<task-id>/builder/<view>-<width>[-dark|-reduced].png`.
- Widths 1440, 768, 375 (and 320 for reflow). Dark mode if the app supports it.
- `browser_emulate_media` with `reducedMotion: "reduce"` for any view with motion.
- Real-ish data, plus one worst case (long German name, unbroken URL, 0 / 1 / 500 items).
- Console clean (no errors/warnings you introduced).
Run these checks on the screenshots; iterate if one fails:
- **Squint**: hierarchy readable, nothing harsh jumps out.
- **Swap**: swap for a stock template, would anything change? Where not, you defaulted.
- **Token**: every value traces to a token in the theme or `system.md`.
- **States**: each interactive element has all five states; data has loading/empty/error.
- **Remove one accessory** (Chanel): delete one decoration that does not serve the task.

## 6. Close the loop
- Update `docs/ux/system.md`: new decisions as `proposed`, patterns used twice with
  measurements, motion ideas you could not ship yet under "Motion backlog".
- In your report: what changed, screenshots (paths), checks run with real pass/fail counts,
  checklist rows you believe pass, open questions. Never claim a test ran if it did not.
