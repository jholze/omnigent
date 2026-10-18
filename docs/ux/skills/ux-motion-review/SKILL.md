---
name: ux-motion-review
description: Review motion code and live animations in a team-chat diff against the project motion tokens and Emil Kowalski's craft bar - ten standards, escalation triggers, frame-budget diagnosis, Before/After/Why table, Block/Approve. Read-only.
---

<!-- Adapted for Omnigent from emilkowalski/skills review-animations (+STANDARDS.md) and
animation-performance (MIT, (c) 2026 Emil Kowalski). Values replaced by docs/ux/system.md
tokens; see docs/ux/NOTICES.md. -->

# Motion review

Default to flagging; approval is earned. A transition that runs but feels sluggish,
comes from the wrong origin, fires too often or drops frames is a regression.
Tokens and limits: `docs/ux/system.md` (NFR-007/008, FR-078/079/089). Motion rows are
`n/a` until `web/src/chat/motion.ts` exists, except "no motion in a task that forbids it".

## Ten standards (violation = finding)
1. Justified: purpose is feedback, spatial consistency, state, or preventing a jarring change.
2. Frequency-appropriate: none on keyboard/100+/day actions (send, channel shortcut, palette).
3. Easing = tokens; `ease-in` on UI is a block; no curve outside the token table.
4. Durations = tokens; UI < 300 ms; nothing > 400 ms except infinite indicators; feedback ≤ 200 ms.
5. Origin: popovers from trigger (Radix `--radix-*-transform-origin`), dialogs centred; never
   from `scale(0)` (spec exception `spring.pop` from 0.4 on the TaskSteps icon).
6. Interruptible: rapid triggers use transitions/springs, not restart-from-zero keyframes.
7. GPU only: `transform`/`opacity` (+`clip-path`); Motion `x`/`y`/`scale` shorthands during
   streaming are a performance finding.
8. Accessibility: reduced motion = 0 transforms, opacity ≤ 120 ms; hover motion gated
   `(hover: hover) and (pointer: fine)`.
9. Asymmetric: deliberate phase slower, response snappy; exit ≈ 0.7 × enter.
10. Cohesion: crisp workbench personality; springs overshoot ≤ 4 px; when unsure, delete it.

## Escalate on sight
`transition: all` · `scale(0)` or pure fade where a small transform belongs · `ease-in` ·
motion on keyboard actions · durations off-token · centre origin on popovers · keyframes on
toasts/badges · animating `width/height/top/left/margin/padding` (accordion height excepted) ·
`setState` per frame · parent CSS variable driving child transforms · missing reduced-motion
· ungated hover · everything entering at once (30–80 ms stagger) · enter animation replayed
on backfill/scroll-back.

## Frame budget (NFR-006: 60 fps, < 5 % dropped frames, no long task > 50 ms during an enter)
Diagnose in order: wrong property → animation coinciding with busy main thread (streaming,
fetch, route change; move to CSS/WAAPI) → React re-render per frame → cost grows with
content (inherited CSS var, too many nodes) → animated CSS variable → blur > 8 px →
only then `will-change`. Say which findings are verified (trace via `test_motion_perf.py` or
DevTools via Playwright) and which are inferred from code. A fast MacBook proves nothing.

## Remedy preference
Delete → reduce → fix easing token → fix origin → make interruptible → move to GPU →
asymmetric timing → polish → a11y gating.

## Output
1. Table (never a Before:/After: list):
| Before | After | Why |
| --- | --- | --- |
| `transition: all 300ms` (`MessageItem.tsx:41`) | `transition: opacity 180ms var(--ease-out)` (`dur.enter`, `ease.out`) | `all` animates unintended layout props; 300 ms is over the enter band |
2. Verdict by tier: feel-breaking → missed simplifications → performance → interruptibility &
   timing → origin & cohesion → accessibility. End with **Block** or **Approve**.
