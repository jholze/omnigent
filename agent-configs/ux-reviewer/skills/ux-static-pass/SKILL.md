---
name: ux-static-pass
description: Fast static review of changed UI files against the Vercel Web Interface Guidelines adapted to Omnigent (a11y, focus, forms, animation, typography, content handling, performance, touch, i18n, anti-patterns), terse file:line output. Use as the first and cheapest pass of a ux-reviewer run.
---

<!-- Adapted for Omnigent from vercel-labs/web-interface-guidelines command.md (MIT,
(c) 2025 Vercel Labs). Next.js-specific rules removed, German UI copy rules added;
see docs/ux/NOTICES.md. -->

# Static pass (file:line)

Scope: changed files under `web/src/**` (`git diff --name-only <base>...HEAD -- web/src`).
Read, check, output terse. Then hand serious items to the design review triage.

## Rules
**Accessibility** icon-only buttons `aria-label` · form controls labelled · `<button>` for
actions, `<a>`/`<Link>` for navigation (no `div onClick`) · images `alt` · decorative icons
`aria-hidden` · async updates `aria-live="polite"` · semantic HTML before ARIA · heading order.
**Focus** visible `focus-visible:` ring · never `outline-none` without replacement ·
`:focus-within` for compound controls · sticky bars must not cover focus.
**Forms / composer** never block paste · Enter behaviour via `isComposerSendKey` · submit
stays enabled until request starts, spinner during · inline errors, focus first error ·
placeholders end with `…` · `spellCheck={false}` on ids/codes · warn on unsaved draft loss.
**Animation** `prefers-reduced-motion` honoured · `transform`/`opacity` only · never
`transition: all` · correct `transform-origin` · interruptible · infinite decorative loops stop
under reduced motion · values only from motion tokens.
**Typography** `…` not `...` · „deutsche“ Anführungszeichen in German copy, “curly” in English ·
loading text ends with `…` ("Lädt…", "Sendet…") · `tabular-nums` on counts/times/durations ·
`text-balance`/`text-pretty` · non-breaking space in `10 MB`, `⌘ K`.
**Content handling** long content: `truncate`/`line-clamp-*`/`break-words` · flex children
`min-w-0` · empty strings/arrays never render broken UI · short/average/very long inputs.
**Images** explicit `width`/`height`; avatars `object-cover` with fallback.
**Performance** lists > 50 items virtualised · no layout reads in render · batch DOM reads/
writes · controlled inputs cheap per keystroke (composer!).
**Navigation & state** URL reflects channel/thread (`/chat/:channelId/t/:threadRootId`,
FR-001) · links support Cmd/Ctrl+click · destructive actions confirm or undo.
**Touch** `touch-action: manipulation` · `overscroll-behavior: contain` in panes/sheets ·
gestures have click/keyboard alternatives · `autoFocus` only desktop, single primary input.
**Layout** `env(safe-area-inset-*)` for fixed bars · no unwanted scrollbars · flex/grid over
JS measurement · `h-dvh`.
**Theming** `color-scheme` matches theme · no raw hex/arbitrary colours.
**i18n** `Intl.DateTimeFormat` / `Intl.NumberFormat` / `Intl.PluralRules` · identifiers and
agent/tool names `translate="no"`.
**Copy (German UI)** active voice, specific verbs ("Freigeben", "Erneut senden" not "OK"),
errors say what to do, no em dash filler, no marketing words; spec labels verbatim (FR-044).

## Anti-patterns (always flag)
`user-scalable=no` / `maximum-scale=1` · `onPaste` + `preventDefault` · `transition: all` ·
`outline-none` without focus-visible · `<div>`/`<span>` click handlers · images without size ·
big `.map()` without virtualisation · inputs without label · icon buttons without
`aria-label` · hardcoded date/number formats · emoji as icons · non-lucide icons ·
`dangerouslySetInnerHTML` on user content · `h-screen`.

## Output
```text
## web/src/chat/MessageItem.tsx
web/src/chat/MessageItem.tsx:42 - icon button missing aria-label
web/src/chat/MessageItem.tsx:67 - transition: all → list properties
## web/src/chat/ThreadPane.tsx
✓ pass
```
No preamble. Explain only when the fix is not obvious.
