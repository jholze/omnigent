---
name: ux-motion
description: Decide whether and how something in the team chat animates, then build it with the project motion tokens (docs/ux/system.md, web/src/chat/motion.ts) - frequency gate, purpose, tool, properties, token, interruption, reduced motion. Includes finding missing animation opportunities. Load before writing any motion code.
---

<!-- Adapted for Omnigent from emilkowalski/skills emil-design-eng, animate and
find-animation-opportunities (MIT, (c) 2026 Emil Kowalski). Values replaced by the
project tokens of NFR-007 / docs/ux/system.md; see docs/ux/NOTICES.md. -->

# Motion for the team chat

Motion should be felt, not watched. Jens wants it slick, which means fast, purposeful and
never in the way. Sometimes the right amount of code is zero.

## Before any motion code
- Is motion in this task's scope? T-006/T-007 say "keine Animationen (T-117)": then ship
  static states (`:active` style without transition is fine) and log the idea in the
  "Motion backlog" of `docs/ux/system.md`. Motion lands with T-117/T-118/T-115.
- Values come only from the token table in `docs/ux/system.md` (mirrored in
  `web/src/chat/motion.ts` once it exists). Missing token? Propose it in `system.md`
  (status `proposed`), do not inline a new curve. Extending tokens is fine; a parallel
  system is a defect.

## The build sequence (in order; steps 1–2 can end with "no animation")
1. **Frequency gate.**
   | Seen | Decision |
   |---|---|
   | 100+/day or keyboard-initiated (send with Enter, channel switch shortcut, command palette, focus jumps) | No animation. Ever. |
   | Tens/day (hover, list navigation, message rows) | Near-imperceptible or nothing |
   | Occasional (thread pane, dialogs, approval card mount, toasts) | Standard token |
   | Rare (first channel, empty state, task completed) | Small delight budget |
2. **Purpose** (name one): feedback · spatial consistency · state indication ·
   preventing a jarring change · delight (rare tier only). "Looks cool" on a frequent
   element is a stop. Data the user is reading (streamed text, numbers) does not move for style.
3. **Cheapest tool that works.** CSS transition (press, hover, colour, class toggles) →
   CSS `@starting-style` (mount enter without JS) → CSS animation (predetermined, must stay
   smooth while the page is busy) → WAAPI → `motion/react` (springs, exit/`AnimatePresence`,
   layout, gestures).
4. **Properties.** `transform` + `opacity` (+ `clip-path`). Never from `scale(0)`: start at
   0.95–0.97 + opacity 0 (spec exception: `spring.pop` from 0.4 on the tiny TaskSteps icon).
   Popovers/menus/tooltips scale from their trigger: Radix exposes
   `var(--radix-popover-content-transform-origin)` / `--radix-dropdown-menu-content-transform-origin`
   / `--radix-tooltip-content-transform-origin`. Dialogs stay centred. Use `translateY(100%)`
   percentages for sheets. While streaming use full `transform` strings in Motion.
5. **Easing and duration = tokens.** Enter/exit and responses: `ease.out`. On-screen
   movement: `ease.inOut`. Constant motion: `linear`. Never `ease-in` on UI. Durations from
   the token table; UI stays < 300 ms, nothing > 400 ms except infinite indicators.
   Springs where motion is interruptible or should feel alive (card mount, TaskSteps);
   overshoot ≤ 4 px (NFR-007), bounce only in the delight tier.
6. **Interruption and exit.** Rapid triggers (toasts, toggles, badges) use transitions or
   springs that retarget, not keyframes that restart. Exit the way you entered, faster
   (≈ 0.7 × enter). Asymmetric where the user decides (hold-to-confirm slow, release fast).
7. **Reduced motion and hover gating ship with it.** `MotionConfig reducedMotion="user"`
   plus `useReducedMotion()` for custom values: transforms off, opacity ≤ 120 ms
   (NFR-008). Hover motion only inside `@media (hover: hover) and (pointer: fine)`.

## Spec-defined motion (FR-078, FR-089, NFR-007): build exactly these values
- Message enter: opacity 0→1, y 8→0 px, `dur.enter` 180 ms, `ease.out`. Only for messages
  arriving live, not on initial load / backfill / scroll-back; stagger 30–80 ms max when
  several arrive together, never blocking interaction.
- Status chip change: crossfade `dur.color` 200 ms (keep width stable, `tabular-nums`).
- Card mount (approval/elicitation): `spring.card`, settle ≤ 300 ms.
- Unread badge change: scale pulse `dur.badge` 150 ms (1 → 1.08 → 1), not on every frame.
- Thread pane open: slide `dur.pane` 220 ms `ease.inOut`, from the edge it lives on.
- TaskSteps (interior.dev pattern, nachbauen ohne Code-Kopie, Q-20): pending dot; active =
  arc `loop.arc` + `loop.shimmer` on label; done = check with `spring.pop`, mono duration
  meta, colour/meta `dur.color`; error = cross with `spring.pop`, following steps stay
  pending; cell change `spring.cell`; sr-only live text "X, Schritt n von m" debounced 500 ms.
- Streaming text (FR-080): buffered release, ≤ 1 visual update per animation frame; keep
  bottom anchoring only if the user was at the bottom, else "Neue Nachrichten" pill.

## Polish moves that are allowed when in scope
- Press feedback: `transform: scale(0.97)` on `:active`, `dur.press`, `ease.out` (0.95–0.98).
- Tooltips: delay the first, then open neighbours instantly without animation.
- Crossfade that looks like two objects swapping: add `blur(2px)` during the swap.
- Group enters: 30–80 ms stagger, decorative only.

## Finding opportunities (read-only mode, when asked "what should animate?")
Sweep: conditional renders with no transition (`{open && ...}`), pressables with no
`:active`, teleporting list changes, popovers without origin, dismissables that exit a
different way, flat rare moments (empty channel, first agent step done). Gate every
candidate (frequency, purpose, budget, function). Report max 5–7 rows:
`| # | file:line | Today | Purpose | Frequency | Suggested motion (token names) |`
plus 2–5 **rejected** candidates with the gate that killed them. Expect to reject most.

## Never ship
`transition: all` · `scale(0)` · `ease-in` on UI · motion on keyboard/100+/day actions ·
UI duration > 300 ms without a spec reason · centre origin on trigger-anchored popovers ·
keyframes on rapidly triggered elements · animated layout properties · ungated `:hover`
motion · missing reduced-motion variant · everything entering at once.

## Output after building motion (3 lines max)
Gate result (tier + purpose) · ingredients (tool, properties, tokens) · what to feel-check
(slow-motion 2–5× in DevTools Animations panel, frame-by-frame, reduced-motion screenshot).
