# Team-chat design memory (`docs/ux/system.md`)

Living design memory for `web/src/chat/**`. The **ux-builder** reads it before every UI
change and updates it when a decision is made or a pattern is used twice. The
**ux-reviewer** treats it as a design contract (alongside the spec). Format adapted from
Dammyjay93/interface-design `.interface-design/system.md` (MIT, see `NOTICES.md`).

Rule of precedence: spec (`specs/omnigent-chat/`: FR/NFR, ADR-0008, task scope) > this file
> skills > personal taste. If this file and the spec disagree, the spec wins and the
builder reports the mismatch instead of silently "fixing" either.

Status of entries: `decided` (Jens or spec), `proposed` (builder, not yet confirmed),
`backlog` (allowed later, not now). Never delete history; mark superseded entries.

## Direction (proposed, confirm with Jens)
- Human: Jens and his team, plus agents posting into channels. Developers who live in
  Omnigent all day, often while agents run. Checking "what is my agent doing, does it
  need me?" between other work.
- Verb: read the thread at a glance, answer an approval, hand a task to an agent.
- Feel: calm workbench. Dense like Linear, quiet surfaces, one accent for "needs you".
- Signature: agent steps rendered as live TaskSteps in the thread (ADR-0008, FR-089):
  spinning arc + shimmer on the active label, pop-spring check, mono duration meta.
- Rejected defaults: SaaS card grid for messages → flat rows with hover surface;
  emoji status → lucide icons with text; colored left border on alerts → icon + text.

## Foundations (decided by the existing app)
- Components: `web/src/components/ui/*` (shadcn-style on `radix-ui`) and
  `web/src/components/ai-elements/*` first. Composer look: `web/src/components/composer/ChatComposer.tsx`
  and `isComposerSendKey` (NFR-011). No second primitive system (no Base UI, no Headless UI).
- Tokens: the app's existing CSS variables / Tailwind 4 theme. No raw hex, no new palette
  without an entry here.
- Icons: `lucide-react` only. 16 px inline, 20 px toolbar, one stroke width per surface
  (NFR-010). Icon-only controls carry `aria-label`; decorative icons `aria-hidden`.
- Class logic: `cn` (clsx + tailwind-merge). Layout height: `h-dvh`, never `h-screen`.
- Depth: borders/hairlines (dense tool); shadows only for floating layers (popover, dialog).

## Motion tokens (decided: NFR-007, FR-078, FR-079; lands in `web/src/chat/motion.ts` with T-117)
Resolution of the Emil Kowalski vs. baseline-ui conflict (ADR-0008): Emil's strong curves
become **project tokens**; baseline-ui's "no custom easing" becomes "no easing or duration
outside these tokens". Until `motion.ts` exists (T-117), tasks whose scope says "keine
Animationen" ship static states only and log ideas in the backlog below.

| Token | Value | Use |
|---|---|---|
| `ease.out` | `cubic-bezier(0.23, 1, 0.32, 1)` / `[0.23, 1, 0.32, 1]` | enter, press, popover, anything responding to the user |
| `ease.inOut` | `cubic-bezier(0.77, 0, 0.175, 1)` | on-screen movement (thread pane slide) |
| `ease.drawer` | `cubic-bezier(0.32, 0.72, 0, 1)` | sheets / drawers on mobile |
| `ease.linear` | `linear` | spinner arc, shimmer, progress only |
| `dur.press` | 140 ms | `:active` scale 0.97 (micro band 120–200 ms) |
| `dur.micro` | 160 ms | hover/colour/tooltip |
| `dur.color` | 200 ms | status chip crossfade, colour/meta change (FR-078, NFR-007) |
| `dur.badge` | 150 ms | unread badge scale pulse (FR-078) |
| `dur.enter` | 180 ms | message enter: opacity 0→1, y 8→0 px (FR-078; band 160–240 ms) |
| `dur.pane` | 220 ms | thread pane open slide (FR-078) |
| `dur.layout.max` | 300 ms | any layout/height change; nothing > 400 ms except infinite indicators |
| `dur.exit` | ≈ 0.7 × enter, `ease.out` | exits are faster, never `ease-in` (overrides plugin87's "ease-in for exits") |
| `spring.card` | settle ≤ 300 ms, overshoot ≤ 4 px | card mount (FR-078) |
| `spring.pop` | stiffness 640, damping 22, mass 0.7, from scale 0.4 | TaskSteps check/cross pop (NFR-007). Spec-sanctioned exception to "never below scale 0.9": tiny icon only |
| `spring.cell` | stiffness 520, damping 34, mass 0.45 | TaskSteps cell change (NFR-007) |
| `loop.arc` | 0.8 s per turn, linear | active step arc |
| `loop.shimmer` | 1.6 s, linear | active label shimmer (existing `ai-elements/shimmer.tsx`) |
| `a11y.announceDebounce` | 500 ms | sr-only "X, Schritt n von m" (NFR-007); channel live region ≤ 1 announcement / 2 s (NFR-009) |
| `reduced` | opacity only, ≤ 120 ms, 0 transforms | `MotionConfig reducedMotion="user"` + `useReducedMotion` (NFR-008, FR-079) |

Mechanics: `motion/react` with `LazyMotion features={domAnimation}` + `m.*` components and
one `MotionConfig reducedMotion="user"` at the chat root. Prefer CSS transitions for
press/hover/colour. While a stream is running (NFR-006: 500 messages + streaming), animate
`transform` strings, not Motion `x`/`y`/`scale` shorthands. Never animate keyboard-driven
actions (send with Enter, channel switch via shortcut, command palette).

## Component patterns (add when used twice; record measurements)
- _empty — builder fills this in, e.g._ `MessageRow — 8px 12px pad · avatar 28 · name 13/600 · body 14/400 · meta 12/400 muted tabular-nums`
- `MessageRow` (proposed, T-007, `web/src/chat/MessageItem.tsx`; channel list + thread pane):
  flat row, no card · `px-4`, header row `pt-2`, compact row `py-0.5` · avatar `size-7`
  `rounded-md` (human: initial on `bg-muted`; agent: `BotIcon` on `bg-secondary` + text
  "Agent") · name `text-sm font-medium` · time `text-xs muted tabular-nums`, `HH:MM` today,
  else `9. Okt., 10:53`, full date in `title` · body `text-sm whitespace-pre-wrap
  wrap-anywhere` · hover/focus-within `bg-muted/50`. Consecutive rows of one author within
  5 min are compact (time in the gutter on hover/focus). Sending: body `text-muted-foreground`;
  failed: `CircleAlertIcon` + "nicht gesendet" + link button "erneut senden" in
  `text-destructive` under the body (FR-084). Reply action: floating `size-7` bordered
  icon button top-right on desktop (opacity 0 → 100 on hover/focus-within, no transition),
  always visible `size-11` on phone; reply count link `text-xs text-primary` with
  `MessageSquareIcon`.
- `ThreadPane` (proposed, T-007): right column `md:w-80 lg:w-96` with `border-l`; header
  `min-h-11` "Thread" + channel label + close `XIcon` link; root row, then a hairline
  divider with "n Antworten"; reply composer at the bottom. Tablet (md–lg): chat sidebar
  hides while a thread is open. Phone: replaces the channel column, own `min-h-11` back link.
- `ChatComposerCard` (proposed, T-007): reuses `ChatComposer` + `ComposerSendButton`
  unchanged; send button `max-md:size-11`; wrapper `px-3 md:px-4`,
  `pb-[max(20px,env(safe-area-inset-bottom))] md:pb-4`.

## Motion backlog (ideas parked until T-117 / T-118)
- _empty_
- T-007: message enter for frames from others (`dur.enter`, opacity + y 8→0); not for the
  own Enter send (keyboard action) and not for backfill.
- T-007: thread pane open slide on desktop (`dur.pane`, `ease.inOut`); phone swap stays instant.
- T-007: reply action toolbar fade on row hover (`dur.micro`, opacity only).
- T-007: "nicht gesendet" → sent: body colour crossfade (`dur.color`).

## Change log
- 2026-10-18: seeded from ADR-0008, NFR-006…NFR-011, FR-078…FR-081 (ux-builder bootstrap).
- 2026-10-20: T-007 MessageRow, ThreadPane, ChatComposerCard patterns (proposed) and four
  motion backlog items.
