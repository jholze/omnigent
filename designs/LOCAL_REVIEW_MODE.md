# ADR: Local review mode (S1-REVIEW)

Status: Proposed. Tracking issue: jholze/omnigent#207. Base: `68a86e80a272`.

## Context

A human wants to review a locally running web app inside the desktop browser
and hand several page comments to an agent as one batch. Today:

- Agents in any session hosted on this machine may open loopback previews
  (`allowsLocalhost` in `web/electron/src/main.js` accepts
  `hostIsThisMachine`).
- Design Mode submits one element plus one crop as a plain chat message, with
  no batch, route, viewport or build id.
- File/PR comments are already persisted with a bounded `anchor_metadata`
  JSON (`omnigent/entities/comment.py`, 1 024-byte cap).

## Decisions

1. **The review flag lives only in the Electron main process.** It is keyed by
   (window, conversation), defaults to off, is never persisted and is never
   stored on the server. It is set only after a native `dialog.showMessageBox`
   confirmation that names the session and origin, triggered by a renderer IPC
   with a recent user gesture from the pinned Omnigent origin. No server route,
   session field/label, agent tool, MCP tool, browser action, page script or
   deep link can set it (FR-S1-003). Rationale: anything the server or agent
   can write, a prompt-injected agent can write.
2. **Exact-origin binding.** While set, the agent policy allows only the bound
   scheme + loopback host + port. Every other loopback port, private,
   link-local, CGNAT, multicast and reserved address stays blocked; DNS and
   connected-address checks remain active for all other hosts. The Omnigent
   server origin and known runner ports are refused as review origins.
3. **Narrow upstream `hostIsThisMachine` (decision A).** Without the flag,
   local-machine sessions block localhost again. This diverges from upstream
   and is accepted; isolation is kept in small modules (`reviewMode.js`) with
   minimal hooks in `main.js`/`browserViewRegistry.js` to ease upstream syncs.
   Arca contexts are unchanged.
4. **Clearing.** The flag clears on review-off, host switch, window close, app
   restart and an 8 h TTL; the agent view is revoked to `about:blank`.
5. **Reuse the comments store.** Page comments use a new discriminated
   `PageReviewAnchor` (`kind: "page"`, `batch_id`, route with sensitive query
   values redacted, viewport/DPR/preset, element, build SHA or `unknown`,
   screenshot file ref) inside `anchor_metadata`, capped at 1 024 bytes with
   deterministic truncation. No migration unless the JSON column proves
   insufficient. `list_comments` gains a `batch_id` filter and returns free
   text wrapped as untrusted data.
6. **Human address bar unchanged.**

## Alternatives rejected

- Server-side session flag: settable by agents/API; violates decision 1.
- "Any loopback" instead of exact origin: lets the agent capture Omnigent's
  own server or other local services.
- New comment table: duplicates an existing, tested store.

## Consequences

- Main-process changes need a desktop build from this fork or a from-source
  run; renderer and server changes ship through the server.
- Normal local sessions lose today's localhost previews (intended).

## Slices

0 ADR · 1 store + native dialog · 2 policy hook · 3 server page comments ·
4 renderer toolbar/staging/send · 5 viewport presets · 6 GUI test + docs.
Each slice is one PR with the ACs of the tracking issue.
