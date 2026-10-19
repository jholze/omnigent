---
name: ux-stack-rules
description: Hard MUST/NEVER rules for UI code in Omnigent's web app (React 19, Tailwind 4, radix-ui, motion/react, lucide-react) - components, interaction, animation performance, accessibility. Load for any UI edit or static UI review.
---

<!-- Adapted for Omnigent from ibelick/ui-skills baseline-ui, fixing-motion-performance and
fixing-accessibility (MIT, (c) 2026 Julien Thibeaut). Rules rewritten for this stack;
see docs/ux/NOTICES.md. -->

# Stack rules (team chat)

Precedence: task scope and spec (FR/NFR, ADR-0008) > these rules > taste.
"Explicitly requested" below means: the task, an FR/NFR, ADR-0008 or `docs/ux/system.md`
asks for it.

## Stack
- MUST use the app's existing Tailwind 4 theme / CSS variables; no raw hex, no arbitrary
  values unless the same value already exists in the codebase.
- MUST use `motion/react` (package `motion`) for JS animation, via `LazyMotion` +
  `domAnimation` and `m.*` components; one `MotionConfig reducedMotion="user"` at the chat root.
- SHOULD use CSS transitions / `tw-animate-css` for press, hover, colour and simple enters.
- MUST use `cn` (clsx + tailwind-merge) for class logic.
- MUST use `lucide-react` icons only: 16 px inline, 20 px toolbar, one stroke width per surface.

## Components
- MUST use existing primitives first: `web/src/components/ui/*` (on `radix-ui`) and
  `web/src/components/ai-elements/*`.
- NEVER mix primitive systems in one surface; NEVER add Base UI / Headless UI / React Aria
  next to Radix.
- NEVER rebuild keyboard or focus behaviour by hand when a Radix primitive exists.
- MUST give icon-only buttons an `aria-label`; decorative icons `aria-hidden="true"`.

## Interaction
- MUST show visible feedback within 100 ms for every click (pressed state, spinner or
  optimistic change; NFR-030).
- MUST use an `AlertDialog` (or undo window) for destructive or irreversible actions.
- SHOULD use structural skeletons for loading; spinners only for short waits.
- NEVER `h-screen`; use `h-dvh`. MUST respect `env(safe-area-inset-*)` for fixed elements.
- MUST show errors next to where the action happened (e.g. "nicht gesendet · erneut senden"
  on the message, FR-084), not only as a toast.
- NEVER block paste in inputs or the composer.
- MUST keep the composer usable while sending/streaming; send key = `isComposerSendKey`.

## Animation (performance + restraint)
- NEVER add animation unless explicitly requested (see precedence); otherwise log it in
  the "Motion backlog" of `docs/ux/system.md`.
- NEVER introduce an easing curve or duration that is not a token in `docs/ux/system.md` /
  `web/src/chat/motion.ts` (project-token rule, replaces baseline-ui's "no custom easing").
- MUST animate compositor properties only (`transform`, `opacity`); `clip-path` is allowed
  for reveals. NEVER animate `width`, `height`, `top`, `left`, `margin`, `padding`
  (exception: accordion height via Radix collapsible CSS vars, ≤ 300 ms).
- NEVER `transition: all`; list properties.
- NEVER exceed 200 ms for interaction feedback; no UI animation > 400 ms except infinite
  indicators (NFR-007).
- MUST pause looping animations off-screen (IntersectionObserver) and stop them when done.
- MUST honour `prefers-reduced-motion`: 0 transforms, opacity ≤ 120 ms (NFR-008).
- NEVER interleave layout reads and writes in a frame; NEVER drive animation from
  `scroll` events or `scrollTop`; measure once, animate with transforms (FLIP).
- NEVER `setState` per animation frame; write to a ref or a motion value.
- NEVER animate an inherited CSS variable on a parent to move children.
- Keep animated `blur()` ≤ 8 px, one-shot only; NEVER animate `backdrop-filter` on large surfaces.
- `will-change` only on the animating element, only after an observed 1 px shift or
  measured jank, removed after.
- While streaming, prefer `transform: "translateY(..)"` strings over Motion `x`/`y`/`scale`
  shorthands (main-thread vs. compositor).
- NEVER partially migrate animation APIs inside one component.

## Typography and layout
- MUST use `text-balance` for headings, `text-pretty` for body; `tabular-nums` for counts,
  timestamps, durations. Prefer `truncate` / `line-clamp` with a way to read the full value.
- Flex children with text need `min-w-0`; fixed-size boxes (avatar, icon) `shrink-0`;
  long URLs/emails `overflow-wrap:anywhere` (Tailwind `break-words` / `wrap-anywhere`).
- NEVER change `letter-spacing` outside existing tokens.
- MUST use the existing z-index scale (no arbitrary `z-[..]`). SHOULD use `size-*` for squares.
- NEVER use `useEffect` for what can be render logic.

## Design
- NEVER gradients, glow, or multicolour accents as affordance. (Exception: the existing
  `ai-elements/shimmer.tsx` text shimmer on the active step, ADR-0008.)
- One accent per view; colour communicates status/action, never decoration.
- Empty states have exactly one clear next action (FR-081 suggestions count as that action).

## Accessibility (critical first)
1. Names: every control has an accessible name; inputs labelled; links meaningful.
2. Keyboard: everything reachable by Tab, visible `focus-visible` ring, no `tabindex > 0`,
   Escape closes overlays, no keyboard traps.
3. Focus & dialogs: trap focus in modals, set initial focus, restore to the trigger.
4. Semantics: native elements before ARIA; lists are `ul/ol`; no skipped heading levels.
5. Forms & errors: `aria-describedby` to the error, `aria-invalid`, required announced.
6. Announcements: new messages in the open channel via `aria-live="polite"`, batched
   ≤ 1 per 2 s (NFR-009); status changes announced; `aria-busy` while loading;
   expandables use `aria-expanded` + `aria-controls`; toasts never the only signal.
7. Contrast ≥ 4.5:1 text, ≥ 3:1 UI/icons/focus ring (check with `docs/ux/scripts/contrast.py`);
   hover-only actions need a keyboard path; disabled not by colour alone.
8. Motion/media: reduced motion respected; no autoplay with sound.
Tool boundary: minimal targeted fixes; no ARIA where native semantics already work.
