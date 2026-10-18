# Chat UX review checklist

The UX reviewer uses this list on every team-chat UI pull request. The
reviewer does not change product code. Findings go back to the task that
owns the surface.

Sources, documentation only. Do not install plugin hooks, do not run the
plugin's `scripts/`, and do not copy interior.dev via the shadcn registry
while the license question is open.

- plugin87/ux-ui-agent-skills `taste/motion-choreography.md`
- plugin87/ux-ui-agent-skills `components/icon-system.md`
- plugin87/ux-ui-agent-skills `.claude/skills/a11y-audit/SKILL.md` and
  `.claude/rules/accessibility.md` (P0 and WCAG 2.2)
- Omnigent chat spec on branch `docs/import-omnigent-specs`, folder
  `specs/omnigent-chat/`: ADR-0008, NFR-006 through NFR-011, NFR-027,
  NFR-030, FR-078, FR-089, FR-090

Product numbers below win when they are tighter than the plugin vocabulary.
The plugin durations (fast 100 ms, base 200 ms, moderate 300 ms, slow 500 ms)
are the grammar. Chat motion uses the tokens in the Motion section.

## How to mark a row

- `ok` — checked against the diff and a screenshot.
- `n/a` — the pull request does not contain that surface yet. Say which
  later task owns it. `n/a` is not a failure.
- `finding` — severity `blocker`, `medium`, or `nit`, plus the screenshot
  path and the file when one is known. An open `blocker` stops the merge.

Motion rows stay `n/a` until `web/src/chat/motion.ts` exists. Step-list rows
stay `n/a` until the thread shows steps. Composer rows stay `n/a` until the
composer is in the diff.

## Screenshots

Save PNGs under `/tmp/omnigent-chat-ux/<task-id>/`. Do not commit them.
Drive the page with Playwright in the named worktree. Turn `team_chat` on
only inside that test process (`OMNIGENT_FEATURES=team_chat` on the fixture
`team_chat_server`). Do not enable the flag on a server that is already
running for someone else.

For each surface the diff touches, capture one desktop shot at 1280×800:

- `/chat` with the flag off
- `/chat` with the flag on
- a channel route, a thread route, and an agent thread when those routes
  are in the diff

When the diff adds motion, capture the same view again with
`reduced_motion="reduce"`. Name files `flag-off.png`, `flag-on.png`,
`channel.png`, `thread.png`, `agent.png`, `reduced-motion.png`.

A review without the shots for the surfaces in the diff is not a pass.

## Shell and copy

- [ ] With the flag on, the main sidebar shows one entry labelled `Chat`
      (`data-testid="chat-nav-entry"`). The old control labelled
      `Session chat` is not added again.
- [ ] With the flag off, that entry is absent, `/chat` shows the sentence
      `Chat ist auf diesem Server nicht aktiviert`, and the page does not
      call `/v1/chat/*`.
- [ ] The chat sidebar uses the headings `Channels` and `Direktnachrichten`.
      A channel name is shown with a leading `#` when the name has none.
      The direct-message heading stays visible when the list is empty.
- [ ] Routes `/chat`, `/chat/:channelId`, and
      `/chat/:channelId/t/:threadRootId` render inside the existing app
      shell. The agent session page at `/` and `/c/:id` is unchanged.
- [ ] Empty and error states use a sentence, not color alone.
- [ ] Click or key activation shows visible feedback within 100 ms
      (pressed state, spinner, or optimistic change). NFR-030.

## Motion

From `taste/motion-choreography.md`, bound to ADR-0008. Animate only
`transform` and `opacity`. Motion has one job: guide attention, show where
something came from, or confirm an action. One focal motion per view. No
routine animation longer than 400 ms. Infinite indicators (spin, shimmer)
are the exception. Entrances use ease-out and travel at most 8–16 px.
Exits are faster than entrances. Springs are for the check, the cross, and
the card, not for every row.

Tokens, once `web/src/chat/motion.ts` exists. Durations are milliseconds.

| Token | Value |
| --- | --- |
| micro | 150 |
| enter | 180 |
| chip | 200 |
| pane | 220 |
| layout | 280 |
| easeOut | 0.2, 0, 0, 1 |
| spring | stiffness 500, damping 40 |
| pop | stiffness 640, damping 22, mass 0.7 |
| cell | stiffness 520, damping 34, mass 0.45 |
| spin | 0.8 s linear |
| shimmer | 1.6 s linear |

- [ ] Chat motion imports these tokens. No one-off duration or easing in a
      component.
- [ ] `LazyMotion` with `domAnimation`, and `MotionConfig reducedMotion="user"`.
- [ ] Message enter is opacity 0 to 1 and y 8 to 0 over the enter token.
- [ ] A status-chip change crossfades over the chip token.
- [ ] A card mount uses the spring and settles within 300 ms, overshoot at
      most 4 px.
- [ ] An unread-badge change pulses over the micro token.
- [ ] The thread pane opens with a slide over the pane token.
- [ ] Under `prefers-reduced-motion: reduce` there is no translate, scale,
      rotate, or shimmer. Opacity fades last at most 120 ms. NFR-008.
- [ ] Loading uses a skeleton that matches the final layout, or the spin
      token. The spinner does not rotate under reduced motion.

## Icons

From `components/icon-system.md` and NFR-010.

- [ ] Chat icons come from `lucide-react` only. One stroke weight. No
      mixed outline and solid styles except a solid icon for a selected
      state.
- [ ] Inline icons are 16 px. Toolbar icons are 20 px. Color is
      `currentColor` or a theme token. No hard-coded hex on an icon.
- [ ] A decorative icon next to a text label is `aria-hidden`.
- [ ] An icon-only control has an accessible name and a target of at least
      24×24 px.
- [ ] Status is text plus icon. The chip reads `startet…`, `arbeitet`,
      `braucht Antwort`, `fertig`, `Fehler`, or `entlassen`. Suggested
      icons: `Loader2`, `CircleHelp` or `MessageCircleQuestion`,
      `CheckCircle2`, `XCircle`, `MoonStar` or `LogOut`, and `Bot` for an
      agent-account badge.

## Accessibility

P0 from the plugin rule, plus NFR-009. Do not claim a contrast ratio you
did not measure. The plugin contrast scripts stay unused. When
`axe-playwright-python` is available, run it on the `/chat` views in the
diff and attach the summary. Until that dependency exists, say contrast
was not measured and do not mark the contrast row `ok`.

- [ ] Tab reaches every control in the journey. Enter or Space activates it.
- [ ] Focus is visible and at least 3:1 against the adjacent surface. A
      sticky bar does not cover the focused control (WCAG 2.4.11).
- [ ] The accessible name, role, and state match the visible control.
- [ ] Text contrast is at least 4.5:1. UI boundaries and icons that carry
      meaning are at least 3:1.
- [ ] Targets are at least 24×24 px (WCAG 2.5.8).
- [ ] New messages in an open channel use `aria-live="polite"`, at most one
      announcement per 2 seconds.
- [ ] A status change is announced.
- [ ] axe reports no `serious` or `critical` violation on the `/chat` views
      in the diff, or the row says the runner is not installed yet.

## Steps

Only when the thread shows a step list. Reference is interior.dev TaskSteps
(ADR-0008). Rebuild it. Do not vendor the registry JSON while the license
is open. Icons are lucide `Check`, `X`, and `LoaderCircle`.

- [ ] Statuses are pending (dot), active (arc plus shimmer on the label),
      done (check with the pop spring and a mono duration), and error
      (cross with the pop spring). Later steps stay pending after an error.
- [ ] An sr-only `role="status"` region says `{Label}, Schritt {n} von {m}`
      after at most 600 ms, debounced about 500 ms. The active row has
      `aria-current="step"`. Completion says `Lauf abgeschlossen` or
      `Lauf fehlgeschlagen bei {Label}`.
- [ ] Reduced motion drops scale, rotation, and shimmer.

## Composer

Only when the composer is in the diff (NFR-011).

- [ ] The composer uses the card treatment of
      `web/src/components/composer/ChatComposer.tsx` and sends with
      `isComposerSendKey`.

## Report

Write this into the session report. The lead pastes it into the pull
request. Do not commit the note and do not edit the product files.

```
UX review against docs/ux/chat-review-checklist.md
Task: <id>
Screenshots: /tmp/omnigent-chat-ux/<task-id>/

| Row | Status | Evidence |
| --- | --- | --- |
| ... | ok / n/a / finding | path or reason |

Blockers: none | <list>
```
