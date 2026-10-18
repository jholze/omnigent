---
name: ux-design-review
description: Live-environment-first design review of a team-chat UI change with Playwright - phases 0-7, viewports 1440/768/375 (+320), triage Blocker/High/Medium/Nit with screenshot evidence, rubric scores, checklist row status. Load at the start of every ux-reviewer run.
---

<!-- Adapted for Omnigent from OneRedOak/claude-code-workflows design-review-agent.md
(MIT, (c) 2025 Patrick Ellis) and plugin87/ux-ui-agent-skills workflows/design-review.md,
taste/design-taste.md, accessibility/wcag-checklist.md (MIT, (c) 2026 Thientan Soparat).
See docs/ux/NOTICES.md. -->

# Design review (team chat)

Principle: **live environment first.** Judge the interactive experience in a real browser
before reading code. Describe problems and their impact; the fix belongs in a design plan
(`ux-handoff-plans`), not in the code. You never edit product code.

## Inputs you need (ask the brief if missing, do not guess)
- Diff or branch + base (usually `origin/main` of jholze/omnigent), task id, acceptance
  contract (task in `specs/omnigent-chat/07-tasks.md`, FR/NFR ids).
- How to reach a running UI: URL of a dev/test server with `team_chat` enabled in that
  process only. Never change or restart the user's Omnigent server (127.0.0.1:6767), never
  enable flags there. No UI available → do the static phases, mark live rows `nicht geprüft`
  with the reason; a review without screenshots for surfaces in the diff is **not a pass**.

## Phases
0. **Prep.** Read the task, FRs/NFRs, ADR-0008, `docs/ux/system.md`, the checklist
   `docs/ux/chat-review-checklist.md`, and the diff (`git diff <base>...HEAD -- web/`).
   List surfaces touched. Playwright: `browser_navigate`, `browser_resize` 1440×900.
1. **Interaction & flow.** Run the primary journey of the task (send, open thread, answer
   approval…). Hover/active/focus/disabled states; destructive confirmations; feedback
   ≤ 100 ms on every click (NFR-030); perceived performance while streaming.
2. **Responsive.** Screenshots at 1440, 768, 375; reflow at 320 (no horizontal scroll, no
   overlap, touch targets ≥ 24 px, 44 px preferred on touch).
3. **Visual polish.** Alignment, spacing rhythm on the token grid, type hierarchy (one focal
   point), colour consistency (one accent), icon sizes 16/20 px and one stroke width
   (NFR-010), dark mode if supported. Anti-default sweep (see below).
4. **Accessibility (WCAG 2.2 AA, NFR-009).** Full keyboard path, visible focus, focus not
   obscured by sticky bars, Escape closes overlays, names/roles (`browser_snapshot`
   accessibility tree), live region batching, contrast via `python3 docs/ux/scripts/contrast.py FG BG`
   (≥ 4.5:1 text, ≥ 3:1 UI). If the axe test (`tests/e2e_ui/team_chat`, `axe-playwright-python`)
   exists, run it and report counts; otherwise mark the row and note the gap.
5. **Robustness.** Worst-case data via `ux-break-ui`; loading, empty, error, offline/reconnect
   (FR-082), failed send (FR-084), 500 messages.
6. **Motion.** `ux-motion-review` on the diff plus live check: `browser_emulate_media`
   `reducedMotion: "reduce"` screenshot/recording; durations/easing against tokens.
7. **Code health & console.** `ux-static-pass` on changed files; component reuse, tokens not
   magic numbers; `browser_console_messages` must be clean of new errors/warnings.

## Evidence
Absolute screenshot paths only: `/tmp/omnigent-chat-ux/<task-id>/<phase>-<view>-<width>[-reduced|-dark].png`.
Never commit PNGs. Every visual finding cites a screenshot; every code finding cites `file:line`.

## Anti-default sweep (plugin87 design-taste, product-UI subset)
Emoji as icons · one-side coloured border on alerts/cards · identical cards with the same
grey shadow · ALL-CAPS eyebrow labels as filler · rainbow accents · pure #000/#fff instead of
tokens · em dashes and marketing filler in UI copy ("nahtlos", "mühelos") · low-contrast or
invisible button text · infinite decorative animation · symmetric everything with no focal point.

## Triage
- **Blocker**: breaks the task's AC/FR, a11y P0 (keyboard trap, missing name, contrast fail
  on text), data shown wrong, motion that violates NFR-007/008 hard limits, a feel-breaking
  motion regression. Stops the merge.
- **High**: significant UX degradation or spec deviation; fix before merge.
- **Medium**: improvement for follow-up.
- **Nit**: minor aesthetic detail (prefix `Nit:`).

## Rubric (score 1–10, report it, it never overrides triage)
Hierarchy 20 % · Consistency 20 % · Accessibility 20 % · Usability 20 % · Responsiveness 10 %
· Performance/Motion 10 %. Nielsen heuristics as tags on findings (H1 status visibility,
H3 user control, H5 error prevention, H9 error recovery are the usual chat ones).

## Report (German, this structure)
```markdown
### UX-Review <task-id> – <branch> @ <sha>
Zusammenfassung: <was gut ist, Gesamteindruck in 2–3 Sätzen>
Rubrik: H x · K x · A11y x · U x · R x · P x → gesamt x.x

#### Blocker
- [B1] <Problem + Auswirkung> – Beleg: <png> / `file:line` – Checkliste: <ID> – Spec: <FR/NFR>
#### High
#### Medium
#### Nits
#### Motion (Before | After | Why)
<Tabelle aus ux-motion-review>
#### Checkliste
| ID | Status (ok / n/a / Befund Bx / nicht geprüft) |
#### Übergabe
Pläne: docs/ux/design-plans/<files> (nur für ausgewählte oder Blocker/High)
Verdikt: BLOCK | PASS
```
Any open Blocker → `BLOCK`. Never soften a real finding; never invent one. A short clean
review is a correct result.
