# Shared-session human chat

Status: proposal. The walking skeleton on `feat/session-human-chat` (`616ea2f`,
[jholze/omnigent#4](https://github.com/jholze/omnigent/pull/4),
[jholze/omnigent#3](https://github.com/jholze/omnigent/issues/3)) is the
baseline. This document is the product contract for the channel after that
skeleton.

Each requirement is marked:

- **Exists** — shipped in `616ea2f`.
- **Adapt** — that code changes to match this contract.
- **New** — not in the branch.

Issue #3 stays the merge bar for PR #4. Where this document disagrees with
that ticket, the disagreement is called out and the tests move only when the
phase that contains it is implemented. The skeleton is not rewritten by this
file landing.

## 1. Decision

Human chat is a channel for the people who share one Omnigent session. It is
useful only when **Share session** is in effect for that session and at least
two people are participants. Otherwise the control is absent and posting is
rejected. The one exception is a thread that already exists after the room
shrinks back to a single participant: that person can still read, and cannot
post, until the session or the lines are deleted. Section 5 states the
matrix. Posting never succeeds unless the session is still multi-person.

A participant is a direct `session_permissions` row on the root conversation
whose `user_id` is a real principal. The `__public__` row is not a
participant. A person who merely has the page open, including a public-link
viewer in the presence list, is not a participant.

"In the session" means they hold a grant, including when they are offline.
It does not mean two SSE connections are open at this moment. Presence
already drops a viewer about 15 seconds after the last stream closes
(`_LEAVE_GRACE_S` in `omnigent/server/presence.py`), and `PresenceAvatars`
renders nothing when the caller is alone. Gating the column on that signal
would hide yesterday's lines every time the other person steps away. Live
presence drives "Anna is here" and typing. It does not decide whether the
channel exists.

This is the one rule the rest of the spec obeys. Section 19 asks Jens to
confirm it before phase 1 changes the skeleton's visibility tests.

## 2. Goals and non-goals

### Goals

1. Two or more participants can exchange short text while an agent run
   continues, without that text becoming an instruction.
2. The channel is absent on a session that is not shared with another person.
3. The same thread is visible on the root and on every sub-agent of that root,
   the next day, from another browser, after a reload.
4. A public-link visitor gets no column, no line, and no stream event. The
   skeleton's 403 (not 404) stays, so a caller who already probes the URL
   can tell the route exists. The UI never surfaces that.
5. Read, write, and moderate actions follow the session permission levels that
   already exist.
6. Later phases can add typing, read state, edit, delete, and an explicit
   handoff of one line to the agent, without changing the storage key or the
   "the agent does not listen" rule.

### Non-goals

These stay out of every phase in this document:

- Threads, reactions, emoji, message formatting beyond plain text, link
  unfurls, file attachments, voice.
- Direct messages between two people that are not the session's participant
  set. The channel is the session, not a pair.
- A second agent conversation. That product already exists (`sidechat`,
  `/side`, `/btw`).
- File-anchored review comments. Those stay on `CommentsPanel`.
- Putting human lines into the agent prompt, the transcript export, or
  `omnigent session import`.
- Using the native harness TUI (Claude, Codex, Pi, Antigravity, and the rest)
  as the input box. Anything typed into that TUI is an instruction.
- Chat on a session the caller cannot already open.
- A guest role that is different from Read plus the public-link sentinel.
- Mobile layout in the first three phases. The phone keeps today's session
  menu until phase 11.
- Server-side unread, typing, edit, and delete in the skeleton. They have
  their own phases.

### What issue #3 already deferred, and where it went

| Deferred by issue #3 | This spec |
| --- | --- |
| Threads, reactions | Non-goal |
| Typing | Phase 4, **New** |
| Edit, delete | Phase 7, **New** |
| Server unread, dot that survives a second browser | Phase 5, **New**. The client dot **Exists** |
| A control that copies a line into the agent composer | Phase 10, **New**, and only behind an explicit confirm |
| Public-link readers | Still forbidden. **Exists** |
| A note on a session that was never shared | Still forbidden. Phase 1 makes posting fail too. **Adapt** |
| Mobile sheet | Phase 11, **New** |
| Audio, DMs | Non-goal |
| TUI | Phase 12 is a separate HTTP client. The harness TUI stays the agent's |

## 3. Baseline

### 3.1 Branch

`feat/session-human-chat` is checked out at
`/Users/jholze/Documents/scripts/omnigent/.work/session-chat` and matches
`origin/feat/session-human-chat` at `616ea2f` ("feat(sessions): add a human
chat column on a shared session"), one commit above fork `main` `87f6f08`.
PR #4 is open against `main`. This spec lives on `docs/chat-integration-spec`,
branched from that commit. It does not push and does not change the PR.

That worktree also has uncommitted edits (a `people` rail tab, a rewrite of
`SessionChatColumn`, and side-chat project-list filtering). Those edits are
not the baseline. A `people` rail tab contradicts issue #3 and section 11 of
this spec. The header control stays.

### 3.2 What the skeleton stores and serves

**Exists.** Table `session_chat_messages`, Alembic revision `sc1a2b3c4d5e`
revising `mm1a2b3c4d5e`. On this branch that revision is the only head.

Columns: `workspace_id`, `root_conversation_id`, `id` (composite primary key),
`created_by` (`String(128)`, null in single-user mode), `created_at` (epoch
microseconds, `BigInteger`), `body` (`Text`). The 2000-character cap is
enforced by the route, not by a check constraint. Index
`ix_session_chat_messages_root_created` on
`(workspace_id, root_conversation_id, created_at, id)`.

Store: `omnigent/stores/session_chat_store/`, named sessions
`omnigent.session_chat_store` / `insert_session_chat_message` /
`list_session_chat_messages`. Append and list only. List is ordered by
`(created_at, id)` ascending and accepts an exclusive cursor of that pair.
There is no limit. A child id is resolved through
`conversation.root_conversation_id` before the permission check, so the check
looks at the root row, not a child row.

Routes, mounted at `/v1` when `create_app(..., chat_store=...)` is used.
`omnigent/cli.py`, `deploy/docker/entrypoint.py`, and
`deploy/databricks/src/app.py` always construct `SqlAlchemySessionChatStore`.

- `GET /v1/sessions/{id}/chat?after=created_at:id` returns
  `{ items, shared }`.
- `POST /v1/sessions/{id}/chat` with `{ body }` returns the stored line,
  status 201. The body is trimmed. Empty and longer than 2000 are 422 and
  write nothing.
- `shared` is true when some principal other than the caller has a direct
  grant on the root. `__public__` is ignored.
- Allowed callers: single-user mode (`permission_store is None`), an admin
  (`permission_store.is_admin` on one of `principals(caller)`), or any direct
  grant on the root for one of those principals. The level is not consulted.
  Any of Read, Edit, Manage, or Owner can post.
- No access at all is 404. A caller who can see the session only through
  `__public__` is 403 with "Human chat is not available on this session".
- `POST` does not create a conversation item, does not call
  `POST /v1/sessions/{id}/events`, and does not enter the agent prompt.

After the insert, `publish_human_chat` emits `session.human_chat` to every
open stream in the tree (root plus up to 20 pages of 100 conversations,
including archived). The event carries `type`, `conversation_id` (the stream
that receives it), `id`, and `created_at`. It does not carry `body`. The SSE
generator drops the event unless `may_read_human_chat` is true, because the
stream route itself only requires `LEVEL_READ`, which a public-link visitor
has. A tree-list failure still publishes to the root stream.

`principals()` (`omnigent/server/principals.py`) returns the cookie subject
plus whatever the process-global resolver adds. On this branch production
never installs a resolver. Tests install one to prove a grant stored on a
linked email counts. `feat/accounts-google-login` (PR #5, issue #1) installs
`account_store.extra_principals` from `create_app`. The two copies of
`principals.py` differ by a noqa comment. Chat must keep calling
`principals()` and must not grow a second identity lookup.

### 3.3 What the skeleton shows

**Exists.** `SessionChatColumn` is rendered from `ChatHeader` immediately
after `PresenceAvatars`, desktop only (`conversationId && !isMobile`).

The control is shown when `GET` returns 200 and `shared` is true or `items`
is non-empty. 403 and 404 hide it. A public viewer in the presence list does
not show it. Single-user mode hides it unless a line already exists.

The column is a fixed `w-72` aside, offset by `--workspace-panel-offset`. It
is not a workspace-rail tab, not `sidechat`, and not `CommentsPanel`. The list
is author, time, text, oldest first. The author string is the raw
`created_by`, or "Someone" when null. The input placeholder is "Message the
people in this session". Enter sends. Shift+Enter inserts a newline. Escape
or Close returns focus to the agent composer (`aria-label` "Message the
agent") and does not insert the draft.

A `session.human_chat` event calls `emitHumanChat`. The column refetches.
While the column is closed the button gets a dot (`data-testid`
`session-chat-unread`) and the tooltip becomes the latest body from that
`GET`. The dot lives in component state. It does not survive reload, a second
browser, or a session switch. There is no sound, no toast, and the column
does not open itself.

Opening a sub-agent shows the same lines because the client calls `GET` with
the child id and the server reads the root.

### 3.4 Neighbouring channels, and why they stay separate

| Channel | What it is | Why it is not this chat |
| --- | --- | --- |
| Agent composer | A turn. The agent hears it and may act. | The whole problem issue #3 describes. |
| `sidechat` rail tab, `/side` | Another conversation with the agent, often sharing the parent runner. A side chat of a filed session is still a session. | It is an agent transcript. |
| `session.btw_sidechat` | Short question to the agent. Not stored. Overlay in the web client. | Ephemeral agent question. |
| File comments (`omnigent/entities/comment.py`, `CommentsPanel`) | Anchored to a file range. Status `draft` or `addressed`. `created_by` is an email. Deleted with the conversation. | Review notes on a file, not a room. |
| `session.presence` / `PresenceAvatars` | Who holds an SSE stream in the tree. Full-state snapshots. Idle when every tab is backgrounded. Leave grace 15s. Renders nothing when `others.length === 0`. Process-local. | Presence is not a message. The dot must not live on the avatars, because they unmount when the other person leaves. **Exists** and stays that way. |
| Share dialog (`PermissionsModal`) | Manage-gated. Levels Read (1), Edit (2), Manage (3), Owner (4). "Anyone with the link, sign-in required" writes the `__public__` row. A separate toggle shares workspace files with view-level collaborators. | Sharing is how someone becomes a participant. It is not the chat surface. |
| `omnigent session export` and the header export | JSONL of `session_meta` plus conversation items. | Human lines are not items, so they are already absent. Keep them absent. |
| OS notifications (`useIdleNotifications`) | Agent turn settled to idle, or a new elicitation. Dock badge from unseen sessions plus pending elicitations. | An agent-finished cue. A chat line must not reuse it. |

### 3.5 Permission and sharing machinery the chat sits on

**Exists**, unchanged by the skeleton except that chat reads it.

- `LEVEL_READ = 1`, `LEVEL_EDIT = 2`, `LEVEL_MANAGE = 3`, `LEVEL_OWNER = 4`.
  Comparison is `>=`.
- `check_session_access` walks parent conversations, then
  `permission_store.check_access`, which accepts a direct grant or the
  capped `__public__` grant. Admins pass before the lookup. Human chat does
  not call this for the allow decision. It calls `may_read_human_chat`, which
  ignores `__public__` and does not walk to a parent, because the id was
  already resolved to the root.
- `SharingMode` (`OMNIGENT_SHARING_MODE`): `on` (default), `read_only`,
  `restricted_read_only`, `off`. It gates **new** grants. It does not revoke
  grants that already exist. `off` disables the Share button. Public sharing
  has its own kill switch and a max level (read or edit).
- Revoking your own grant is "leave" and needs only read. The owner grant
  cannot be removed through that route.
- Fork copies the caller's new ownership, not the source grants
  (`feature-map/sessions.md`, `fork-access`). Chat rows are keyed by root id,
  so a fork does not see them. Keep that.
- `delete_conversation` deletes the subtree in two transactions. The first
  removes conversation rows, items, labels, and FTS rows. The second is
  best-effort and removes comments, policies, `session_permissions`, and
  conversation metadata. A failure of the second leaves orphans. It does
  not delete `session_chat_messages`. **Adapt**, phase 1: when the deleted
  id is a root (`parent_conversation_id` is null), the metadata transaction
  also deletes chat rows (and, from phase 5, read cursors) whose
  `root_conversation_id` is that id. `delete_ap` already loads the row, so
  it returns that fact with the subtree ids. A child delete skips the chat
  delete. The conversation store may import `SqlSessionChatMessage` the
  same way it already imports `SqlComment`. Both stores share the engine.
- Live fan-out (`omnigent/runtime/session_stream.py`) and presence are
  in-process. A subscriber that falls behind is disconnected. Events published
  with no subscriber are gone. Clients heal by `GET`. There is no replay.
  Multi-worker deployments do not share this bus. That is the same constraint
  presence already has. This spec does not add a cross-process bus.

### 3.6 Tests that already pass on the skeleton

- `tests/server/test_session_chat.py` — owner round-trip; child id reads and
  writes the root; same-microsecond cursor; direct grant including a linked
  principal; `__public__` alone is 403; stranger is 404; admin can post;
  empty, whitespace, and 2001 characters are 422; `POST` writes no
  conversation item; root and child streams each get one bodyless event;
  public viewer gets none; single-user `POST` returns 201 and `GET` returns
  200 with `shared: false`; another principal sets `shared` true;
  `__public__` alone leaves `shared` false.
- `web/src/shell/SessionChatColumn.test.tsx` — hidden on 403, 404, and
  `shared: false` with no items; shown when `shared` or items are non-empty;
  column is not side chat and not comments; Enter posts to `/chat`; a closed
  column sets the dot and takes the tooltip from the next `GET`; Escape
  returns focus to the agent composer.
- `tests/e2e_ui/collaboration/test_session_human_chat.py` — two browsers,
  line stays out of the agent transcript, the other browser sees the dot,
  reply appears without reload, reload of the root and of a sub-agent shows
  both lines.

## 4. Personas and use cases

### Personas

| Persona | Grant | What they need |
| --- | --- | --- |
| Owner (Jens) | Owner on the root | Share the session, talk beside a running agent, read the thread the next morning, remove a line that should not stay. |
| Collaborator (Anna) | Edit or Manage | See Jens's avatar, answer "I'll look at the test" without the agent taking that sentence as work. |
| Reviewer (Sam) | Read | Follow the transcript and the human thread. Talk to the people. Must not start an agent turn and must not hand a line to the agent. |
| Public visitor | `__public__` only | Open the shared transcript when the link allows it. No chat control, no event, no hint. |
| Stranger | No row | 404 on the session and on `/chat`. |
| Admin | `is_admin`, maybe no row | Support access to sessions they can already open. Reading the human thread is part of support. Posting into a private session is not. |
| Solo user | Owner only, or single-user mode | No control. The product is a shared session, and they are alone. |

There is no separate guest principal. A signed-in person with Read is a
participant. A person whose only path in is `__public__` is a visitor.

### Use cases

**UC1. Coordinate while the agent runs.** **Exists** as the walking skeleton.
Anna and Jens both have the session open. Jens sends "I'll look at the test."
The column stays open while the agent streams. Anna, whose column is closed,
sees the dot, opens it, and replies. Neither line is in the transcript. The
agent run is unchanged.

**UC2. Read it the next morning.** **Exists** for storage and for the
sub-agent view. Jens opens the same control on the root or on a sub-agent and
reads both lines. The other person need not be online. Phase 1 keeps this
working as long as the other person's grant is still there.

**UC3. Solo session.** **Adapt.** Jens has not shared. The header has no chat
control. `POST` does not store a line. A public link, with nobody else
granted, behaves the same for the chat even if presence shows a visitor.

**UC4. Share, then the other person is offline.** **Exists** for visibility
(`shared` does not look at presence). The control is there. Sending still
works, so Jens can leave "Picking this up tomorrow" for Anna. The column
shows that nobody else is here right now (phase 3, **New**).

**UC5. Read-only reviewer.** **Exists** for the human post: any direct grant
can already post, and phase 1 keeps that for Read. Sam can post a human
line. Sam's agent composer stays on the existing read-only rules. Sam cannot
use "Ask the agent" (phase 10, **New**). Phase 2 locks the Read-can-post
case so a later edit does not start requiring Edit.

**UC6. Unshare.** **New** behavior. Anna's grant is revoked. She receives 404
on the session, same as today, and so 404 on `/chat`. Jens still has the
owner row. `shared` becomes false. The control switches to read-only while
lines exist, so he can still read what was said. He cannot post until he
shares with someone again. He can delete the thread (phase 7). Until phase 7,
the lines stay readable and no new line can be added.

**UC7. Public link beside a real share.** **Exists.** The session is shared
with Anna and also has `__public__`. Anna and Jens have the channel. A
visitor who opens the link does not.

**UC8. Sub-agent and side chat.** **Exists** for sub-agents: same thread.
A side chat is its own conversation with its own root once it is a real
session. It does not inherit the parent's human thread. People who need to
talk about the parent stay on the parent's channel. Do not special-case
`SIDE_CHAT_LABEL_KEY`.

**UC9. Hand one sentence to the agent.** Phase 10, **New**. Jens selects
"Ask the agent" on a line, confirms that this becomes an instruction, and
the existing turn path sends it. The rest of the thread stays out of the
prompt. Sam on Read does not see the action.

**UC10. Second browser and reload.** Lines **Exist**. The unread dot does
not. Phase 5 adds a server cursor so the second browser shows the dot too.

**UC11. Terminal-first session.** The header, including this control, is
already on screen when the web view is in terminal mode (`ViewModeToggle`
does not remove `SessionChatColumn`). **Exists**, untested. Phase 3 adds the
test. The embedded harness terminal is not an input for this channel.

**UC12. Phone.** **Exists** as absence: the control is not mounted under
`useIsMobileViewport()`. Phase 11 adds a sheet behind the session menu.

## 5. Activation

The server decides. The client renders the decision. It does not re-derive
"is this shared?" from presence.

### 5.1 Terms

- **Participant row.** A direct grant on the root whose `user_id` is not
  `__public__`. The owner row counts.
- **Shared.** **Exists** as `human_chat_is_shared`: some participant row's
  `user_id` is outside `principals(caller)`. This is caller-relative. An
  admin with no row sees `shared: true` on a solo owner's session, because
  the owner row is "someone else". The UI must not use `shared` to decide
  visibility. It uses `access`.
- **Multi-person.** Two or more identity clusters on the root. Build it as
  follows, and do not use `human_chat_is_shared` as a stand-in. An admin
  looking at a lone owner would otherwise count as a second person.
  1. Take every participant row's `user_id`.
  2. For each id, start a set containing that id plus `principals(id)`.
     `principals` resolves forward from the id you pass. With the accounts
     resolver installed, a username gathers its linked email. With no
     resolver, `principals(id)` is `{id}`.
  3. Merge sets that overlap, and repeat until nothing merges. Overlap is
     symmetric even when `principals` is not: if `principals(username)`
     contains the email, the email's set and the username's set merge when
     either is seen.
  4. `multi_person` is true when at least two sets remain.
  A caller who has two rows for themselves (username and linked email)
  collapses to one cluster once the resolver knows they are the same
  account. Two rows the resolver does not connect stay two people. There
  is no reverse directory in phase 1. Document that limit on the helper.
- **Caller row.** Some id in `principals(caller)`, other than `__public__`,
  has a participant row. An admin without a grant does not have a caller row.
- **Visible / writable.** `access` is `read` or `write` / `access` is `write`.

`access`, once the caller has passed the flag, the 404, and the public-only
403, and once single-user mode has been handled:

| `multi_person` | Caller row | Admin | Lines exist | `access` |
| --- | --- | --- | --- | --- |
| no | yes | either | no | `hidden`, `items: []` |
| no | yes | either | yes | `read` |
| no | no | yes | either | `hidden`, `items: []` |
| yes | yes | either | either | `write` |
| yes | no | yes | either | `read` |

"Lines exist" is an internal exists-check. `hidden` still returns no items.
Single-user mode does not use this table: `GET` is `hidden` with `items: []`
and `POST` is 403. The level on the caller row does not matter for `write`.
Read, Edit, Manage, and Owner all post when the session is multi-person.

### 5.2 Matrix

| Situation | `GET` | Control | `POST` |
| --- | --- | --- | --- |
| Flag off | 404 | Hidden | 404 |
| No session access | 404 | Hidden | 404 |
| `__public__` only | 403 | Hidden | 403 |
| Single-user mode | 200, `access: "hidden"`, `items: []` | Hidden | 403 |
| Owner alone, no other grant, no lines | 200, `access: "hidden"` | Hidden | 403 |
| Owner alone, lines left from an earlier share | 200, `access: "read"` | Read-only column | 403 |
| Two or more participants, caller has a direct grant | 200, `access: "write"` | Column and composer | 201 |
| Two or more participants, caller is admin without a row | 200, `access: "read"` | Read-only column | 403 |
| `SharingMode=off`, but two grants already exist | 200, `access: "write"` for those participants | Column | 201 |
| `SharingMode=off`, owner alone | 200, `access: "hidden"` | Hidden | 403 |
| Other participant offline | Same as when they are online | Column | 201 if `access` is `write` |
| Other participant present | Same, plus presence chrome (phase 3) | Column | 201 if `access` is `write` |
| Caller is Read, Edit, Manage, or Owner and the session is multi-person | `write` | Composer enabled | 201 |
| Child id of a multi-person root | Same as the root | Same column | Writes the root |
| Fork of a chatty session | New root, no copied grants, no copied lines | Hidden until the fork is shared | 403 until then |

`SharingMode` is a gate on creating grants, not a second chat switch. Once
two people have grants, chat works even if an operator later sets sharing to
`off`. They cannot add a third person while it is off. That matches how
grants already behave.

### 5.3 Response shape

**Adapt** `SessionChatListResponse`. PR #4 is unreleased, so the client and
the server change together.

```json
{
  "items": [
    {
      "id": "abc",
      "created_by": "anna@example.com",
      "created_at": 1700000000000000,
      "body": "I'll look at the test"
    }
  ],
  "shared": true,
  "access": "write"
}
```

- `items` — lines the caller may see, oldest first. Empty when `access` is
  `hidden`, including single-user mode, so a solo server learns nothing from
  a probe. When `access` is `read` or `write`, `items` is the page.
- `shared` — **Exists.** Caller-relative, as today. Not the visibility bit.
  An admin on a solo session can see `shared: true` together with
  `access: "hidden"`.
- `access` — **New.** `hidden`, `read`, or `write`, from the table in
  section 5.1. The client shows nothing on `hidden`. On `read` it shows the
  column and replaces the composer with the sentence in section 12.1. On
  `write` it shows the column and the composer.

`POST` uses the same predicate as `access == "write"`. A client that ignores
`access` and posts anyway receives 403 and stores nothing.

The page object also carries `has_more`, `has_older`, and `next_after`, as
section 10.3 defines them. `hidden` returns `items: []` and all three empty
or false. The column's first request is `tail=1`.

### 5.4 What phase 1 changes in the skeleton tests

These acceptance checks from issue #3 change when phase 1 lands, and not
before:

- Single-user `POST` returns 201 and the control appears once a line exists.
  Becomes: `POST` 403, `GET` `access: "hidden"`, control stays hidden.
- The control is shown when `items` is non-empty even if `shared` is false,
  and the composer still posts. Becomes: non-empty history with a caller row
  on a session that is no longer multi-person is `read`. The composer is
  replaced by the read-only sentence. `POST` of a valid body is 403.
- An admin with no row can `POST`. Becomes: an admin can `GET` with `read`
  on a multi-person session, and `POST` is 403. On a solo session the admin
  `GET` is `hidden` (no peek at a private monologue that should not exist).
  Support read of a real shared thread stays.

The two-browser e2e, the bodyless event, the public-viewer drop, the 404 for
strangers, the 403 for public-only, the child-id root storage, the cursor,
and the 422 cap stay.

## 6. Human channel versus the agent

### 6.1 The agent does not listen

**Exists** and stays the invariant.

`POST /chat` writes `session_chat_messages` only. Prompt assembly
(`omnigent/runtime/prompt.py` and the harness adapters) does not read that
table. Framework instructions are not a back door for injecting the thread.
A regression test loads a session that has human lines and asserts the
composed prompt and the conversation items do not contain those bodies
(phase 8, **New**). The existing test that `POST` creates no conversation
item stays.

The stream event has no body so a log or a proxy that stores SSE frames does
not become a second copy of the thread. **Exists.** Keep it for typing and
edit signals too: those events carry ids and booleans, and the client
refetches.

### 6.2 What a person can say to the agent

The agent composer is the only way to instruct the agent, until phase 10.
Read-only callers already cannot drive that composer. This spec does not
weaken that.

Typing `@agent` in the human composer is ordinary text. It does not start a
turn, does not set a flag on the row, and does not notify a model. Magic
mention parsing is a reliable way to send an instruction by accident, which
is the failure this channel exists to prevent.

### 6.3 Phase 10 handoff

**New.** Optional, off until the phase is implemented. One explicit action,
"Ask the agent", on a line the caller can already see.

1. The caller is Edit, Manage, or Owner on the root. Read does not see the
   action. Public and `access: "read"` admins do not see it.
2. A confirm step states that the agent will see this text and may act. The
   default button is cancel.
3. On confirm, the client sends a normal user turn through the existing
   session event path. The turn body is the line, prefixed with the author's
   display id and the fact that it was forwarded from session chat. The
   server attributes the turn to the person who confirmed, who is the person
   the agent already treats as the user.
4. The rest of the thread is not attached.
5. The human row gains `handed_off_item_id` (nullable) so the column can show
   "Sent to the agent" on that line. A second confirm on the same line is
   allowed only after a second explicit click. It creates a second turn. It
   does not edit the first.
6. Failure of the turn path does not delete the human line. The column
   reports that the agent did not receive it.

No phase sends the thread in the background, summarizes it into the prompt,
or subscribes the agent to `session.human_chat`.

### 6.4 Mentions of people

**New**, and not scheduled. A later phase may highlight a participant's
principal and raise that person's unread. It is still a human line. It is
not specified here because display names depend on the accounts work, and a
wrong parser would notify the wrong person.

## 7. Roles and permissions

The chat does not add a role. It maps onto the levels in
`omnigent/server/auth.py`.

| Action | Read | Edit | Manage | Owner | Admin, no row, session is multi-person | Public | No row |
| --- | --- | --- | --- | --- | --- | --- | --- |
| See the session transcript | yes, existing | yes | yes | yes | yes | yes, if the link grants it | 404 |
| See human chat | write | write | write | write | read | 403 | 404 |
| Post a human line | yes | yes | yes | yes | no | no | no |
| Post an agent turn | no, existing | yes | yes | yes | existing admin rules | existing public rules | no |
| Ask the agent (phase 10) | no | yes | yes | yes | no | no | no |
| Edit own line (phase 7) | yes | yes | yes | yes | no | no | no |
| Delete own line (phase 7) | yes | yes | yes | yes | no | no | no |
| Delete someone else's line (phase 7) | no | no | yes | yes | no | no | no |
| Share / revoke | no | no | yes | yes | existing | no | no |
| Export the human thread (phase 9) | yes | yes | yes | yes | yes | no | no |

Read can post because the human line does not change the workspace and does
not instruct the agent. That is the point of giving a reviewer a place to
talk. **Adapt**: `may_read_human_chat` stays the "has a direct row, or is
admin, or single-user" helper the skeleton has, and phase 1 stops treating
single-user as allow-everything. `access` and `POST` use the table in
section 5.1. `human_chat_is_shared` is not the write predicate. It stays
only as the `shared` field.

Manage and Owner can moderate because they can already decide who is in the
room. Edit cannot delete Anna's line.

Admin read is support. Admin write would let a support principal speak as
themselves inside a customer's room and would also let them create the first
line on a session that is not multi-person. Both are rejected.

Single-user mode has no principals to share with. The routes stay mounted so
the process layout does not fork, and they refuse writes. **Adapt** the
current "permission store is `None`, so allow everything" branch in
`may_read_human_chat`.

A grant stored on a linked Google email counts once PR #5 has installed the
resolver. **Exists** as a call to `principals()`. Until that resolver is
installed, only the cookie subject matches. Merging the two PRs keeps one
`principals.py`. Land the accounts branch first when both are open, as PR #4
already says.

Device-grant and machine credentials that can already call `/v1/sessions`
can call `/chat` only when the subject passes the same rule. A machine
client with no human grant cannot post. No new allowlist entry is required:
`/v1/sessions` is already on the delegated-path list.

## 8. Presence, typing, and read

### 8.1 Presence

**Exists.** Do not build a second presence service. The column reads
`useChatStore.viewers`, which `session.presence` already replaces wholesale.
Filter out the caller, the same way `PresenceAvatars` does.

Phase 3, **New**, adds one line in the column header:

- No other viewer: "No one else is here."
- One or more: their existing initials and idle state. Idle stays dimmed.
- The header control itself stays visible when the avatars unmount. That is
  why the control is a sibling of `PresenceAvatars`, not a child. **Exists.**

Presence is not authorization. A visitor can appear in `viewers` if they hold
a stream. They still fail `may_read_human_chat`. The column must not add
their id to the participant set, and the typing fan-out must not include
them.

Join and leave do not insert system lines ("Anna joined"). Those lines would
persist and would wake unread. Presence already communicates join and leave.

### 8.2 Typing

Phase 4, **New**.

Client, while the column is open and the draft is non-empty: at most one
`typing: true` every 3 seconds, and one `typing: false` when the draft
becomes empty, the column closes, or 4 seconds pass without a key. The
client does not send the draft.

`POST /v1/sessions/{id}/chat/typing` with `{ "active": true | false }`.
Caller must have `access: "write"`. No row is written. The handler publishes:

```json
{
  "type": "session.human_chat.typing",
  "conversation_id": "<stream id>",
  "user_id": "<caller>",
  "active": true
}
```

Same tree fan-out and same public-viewer drop as `session.human_chat`. No
body. The receiving client shows "Anna is typing" and clears it after 4
seconds without a fresh `active: true`, or immediately on `false`. Several
people collapse to "Anna and Jens are typing". The indicator is not shown
for the caller.

The route is not `presence._broadcast`. That helper builds a presence
snapshot and only addresses streams that have a presence connection. Issue
#3 already forbids that for the chat event. Typing uses `publish_human_chat`'s
stream-id walk, with a different event type.

A deployment with more than one server process will miss typing across
processes. That matches presence. Document it on the route. Do not add a bus
in this phase.

### 8.3 Read

Skeleton, **Exists**: the dot is `useState` in `SessionChatColumn`. Opening
the column clears it. A reload does not restore it. A second browser never
had it. The tooltip text exists only after a refetch in that same mount.

Phase 5, **New**, adds a cursor per participant:

Table `session_chat_reads`. Primary key
`(workspace_id, root_conversation_id, user_id)`. Columns `last_read_at`
(microseconds) and `last_read_id`. Upsert moves the cursor forward only.
A cursor behind the stored cursor is ignored, so two browsers racing cannot
mark a newer line unread.

`POST /v1/sessions/{id}/chat/read` with `{ "created_at", "id" }`. The pair
must name a real line on that root. Caller needs `access` `read` or `write`,
including an admin reading a multi-person thread. `access: "hidden"` is 403
and stores nothing. 404 and the public-only 403 match `GET`. A hidden
caller's 200 from `GET` is not a license to post a cursor.

The list response gains, for each item, nothing about other people in the
skeleton. Phase 5 adds `unread_count` on the list response (lines after the
caller's cursor) so a freshly opened browser can paint the dot without
diffing the whole page in private logic. Other participants' cursors are
returned as `readers: [{ "user_id", "created_at", "id" }]` only to callers
with `write` or `read` on a multi-person session. They are the same
principals the owner can already see in the share dialog. Visitors never
receive `readers`.

Event `session.human_chat.read` carries `user_id`, `created_at`, `id`, and
the stream's `conversation_id`. No body. Clients that do not understand it
must not fail the stream. Adding a variant to `ServerStreamEvent` is
required, because an unknown `type` fails the connection. **Exists** as the
pattern used for `session.human_chat`.

The column shows "Seen by Anna" under the latest line Anna's cursor covers.
It does not show per-line receipts on every bubble. One session, one cursor,
one line of text.

Clearing the local dot still happens when the column is opened. The client
then posts the latest line's cursor. If that post fails, the local dot stays
clear for this mount and the server cursor is unchanged, so the other
browser still shows unread. That is the safe direction.

## 9. Notifications

**Exists** and stays for the on-screen case: a dot on the chat control, the
latest line as its tooltip, no sound, no toast over the transcript, no
auto-open. Issue #3 required that, and it is right for a person who is
already looking at the session. `useIdleNotifications` is not this signal.

Phase 6, **New**.

- Fire only when the window is not focused, or the session is not the open
  one. The same "you are looking at it" suppression the agent cue uses.
- Title: the session title. Body, by default: "New message in session chat."
  The line text is not the OS body. Lock screens and notification centers
  are a worse place for the sentence than the column. A later setting can
  opt in to the text. Default is the generic body.
- Do not fire for the caller's own line, for typing, or for a read cursor.
- Do not increment the agent dock badge. A chat dot on the session row in
  the sidebar is phase 6 if the row can show it without a new poll. If it
  cannot, the OS notification and the in-column dot are enough. Do not
  piggyback `pending_elicitations_count`.
- Clicking the notification opens the session and the column. It does not
  focus the agent composer and does not send anything.
- Permission stays the existing lazy browser prompt. Denied stays denied.
  Electron keeps using the OS path `useIdleNotifications` already uses.
- No sound in this phase.

The client-only dot remains the focused-window signal even after phase 6.
The server cursor (phase 5) is what makes the badge right after a restart.
Phase 6 can ship against the client dot first, and become accurate across
browsers once phase 5 exists. The phase list orders 5 before 6 for that
reason.

## 10. Data model

### 10.1 Current row

**Exists.** `SqlSessionChatMessage`.

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | `BigInteger`, PK, default 0 | Same workspace scope as the other tables. |
| `root_conversation_id` | `Uuid16`, PK | The root, never the child the client addressed. |
| `id` | `Uuid16`, PK | Bare uuid hex, generated in the store. |
| `created_by` | `String(128)`, null | Server-set from `attribution_user`. The client cannot send it. |
| `created_at` | `BigInteger`, not null | Epoch microseconds from `now_epoch_us`. |
| `body` | `Text`, not null | Trimmed, 1 to 2000 characters. |

No foreign key, matching `p1a2b3c4d5e6_remove_all_fks`. The application
deletes these rows when the root is deleted.

### 10.2 Additions by phase

All of these are **New** columns or tables. Nullable additions, backfilled
as null, so a deploy does not rewrite old bodies.

| Phase | Change |
| --- | --- |
| 1 | No new column. Behavior change only. Add the delete in `delete_conversation`. |
| 5 | Table `session_chat_reads` as in section 8.3. |
| 7 | `edited_at` microseconds, null. `deleted_at` microseconds, null. `deleted_by` `String(128)`, null. Body of a deleted row is cleared to empty string in the same write, so a database dump does not keep the text. |
| 8 | `client_id` `String(64)`, null. Unique `(workspace_id, root_conversation_id, created_by, client_id)` where `client_id` is not null. |
| 10 | `handed_off_item_id` `String(64)`, null. The conversation item id. No FK. |

`created_at` does not change on edit. Order stays insertion order. `edited_at`
is display only ("edited").

Soft-deleted rows stay so cursors and "a line used to be here" remain stable.
`list_for_root` returns them with `body: ""` and `deleted_at` set. The client
renders "Deleted" and does not offer the text. A cursor that points at a
deleted id still works.

### 10.3 Limits

- Body: trim, reject empty, reject above 2000. Count characters, not bytes,
  the way the pydantic validator already does (`len` on the Python str).
  **Exists.**
- Strip NUL. Reject a body that is only whitespace. **Exists** for
  whitespace via `strip`. NUL is **New** in phase 8, because a NUL in the
  middle survives `strip` and is useless in the UI.
- `GET` limit: default 100, maximum 500. **Adapt**, phase 1. The skeleton
  returns every row after the cursor, oldest first, and has no limit.
  - `after` stays an exclusive ascending cursor `(created_at, id)`. It
    returns newer rows. The column uses it for the delta after an event:
    `after` is the last line it already has. Same-microsecond rows still
    both appear.
  - Initial paint uses `tail=1`. The server returns the newest `limit` rows
    in ascending order, so the client still renders oldest-at-top inside
    the page. `has_older` is true when rows exist before that page.
  - Scroll up uses `before=created_at:id` of the oldest loaded line. The
    server returns the previous page in ascending order. `before` and
    `after` together are 422.
  - `has_more` on an `after` query means newer rows were truncated.
    `next_after` is the cursor of the last returned row, or null.
  The open column must not refetch with a bare `GET` once a tail page is in
  memory. A bare refetch would replace a 100-line tail with the oldest 100
  if someone implemented "no cursor means oldest". Bare `GET` (no `tail`,
  no `after`, no `before`) remains "oldest page", which is what a full
  export-style walk wants. The column's first request is `tail=1`.
- Tree fan-out cap: 20 pages of 100. **Exists.** A session tree larger than
  that still notifies the root and the pages it walked. Phase 8 logs when
  the cap trips. Do not raise it blindly.

### 10.4 What is deliberately not stored

- Typing state.
- Presence.
- The unread dot of a browser that has not posted a read cursor.
- A copy of the line inside `conversation_items`.
- Edit history of previous bodies. An edit replaces `body`. Audit, if it
  records the action, records the id and the actor, not the old text.
  Section 19 asks whether abuse response needs the old text. The default
  here is to drop it.

## 11. API and events

### 11.1 Routes

All ids may be the root or a child. All of them resolve to the root, then
authorize against the root.

| Method and path | Phase | Behavior |
| --- | --- | --- |
| `GET /v1/sessions/{id}/chat` | **Adapt** | Page of lines plus `shared` and `access`. Query: `after`, `before`, `tail`, `limit`. |
| `POST /v1/sessions/{id}/chat` | **Adapt** | `{ "body", "client_id"? }`. 201, or 200 when `client_id` repeats (phase 8). 403 when not writable. 422 on an empty or over-long body. |
| `POST /v1/sessions/{id}/chat/typing` | **New**, phase 4 | `{ "active": bool }`. 204. No row. |
| `POST /v1/sessions/{id}/chat/read` | **New**, phase 5 | `{ "created_at", "id" }`. 204. Cursor moves forward only. |
| `PATCH /v1/sessions/{id}/chat/{message_id}` | **New**, phase 7 | `{ "body" }`. Author only. 422 on an empty body. 403 for everyone else. 409 if `deleted_at` is set. |
| `DELETE /v1/sessions/{id}/chat/{message_id}` | **New**, phase 7 | Author, or Manage/Owner. Soft-delete and clear `body`. 204. |
| `GET /v1/sessions/{id}/chat/export` | **New**, phase 9 | JSONL for a caller with `read` or `write`. Not the transcript export. |

Unknown `client_id` format (not 1–64 chars of `[A-Za-z0-9_-]`) is 422.

Errors use `OmnigentError` the same way the skeleton does. 401 when a
permission store is configured and there is no user. That branch **Exists**.

Body shape is validated by the pydantic model before the handler runs, as
the skeleton already does. An empty or over-long body is 422 even for a
caller who would have been 403. A well-formed body from a caller who is not
writable is 403 and writes nothing. Phase 1 does not move validation into
the handler to invert that order.

`POST` rate: 30 successful-or-attempted posts per minute per `(caller, root)`,
in process. The 31st is 429 and writes nothing. Phase 8, **New**. A second
process has its own window. Good enough while the stream bus is also
in-process. Typing is separate: the client cap in section 8.2 is the limit.
The server drops a typing publish that arrives more than once a second from
the same caller and still returns 204, so a stuck client cannot fan out
without bound.

### 11.2 Events

All of these are added to the `ServerStreamEvent` union. Each is stamped
with the receiving stream's `conversation_id`.

The stream filter **Exists** as an exact match on `"session.human_chat"`
(`_stream_live_events` in `omnigent/server/routes/_sessions/helpers.py`).
`session.human_chat.typing`, `.read`, and `.updated` would pass through to
a public viewer if that match is left as it is. The phase that adds each
type **Adapts** the filter to drop every type that starts with
`session.human_chat` when `include_human_chat` is false. Do that in the
same change as the new variant.

| `type` | Payload besides `conversation_id` | Persisted |
| --- | --- | --- |
| `session.human_chat` | `id`, `created_at` | The row was just inserted. **Exists.** |
| `session.human_chat.typing` | `user_id`, `active` | No. |
| `session.human_chat.read` | `user_id`, `created_at`, `id` | The cursor was just upserted. |
| `session.human_chat.updated` | `id`, `created_at`, `change` = `edited` or `deleted` | The row was just updated. No body. |

The web client maps `session.human_chat` in `web/src/lib/sse.ts` and ignores
it for block rendering (`blockStream.ts` returns on `session_human_chat`).
**Exists.** New types follow that path: a small parser, `emit` to the column,
and an explicit ignore in the transcript reducer so a line never becomes a
bubble in the agent thread.

There is no replay. A client that mounts mid-conversation learns the thread
from `GET`, then applies events. On an event it already has (same `id`), it
does not duplicate. **Exists** for the insert path (`prev.some(id)`).
Updated events refetch that id's page or replace the local row from a
follow-up `GET`. Refetch is the contract. Do not put the new body on the
event.

### 11.3 Idempotency

Phase 8. The column generates one `client_id` per send attempt and reuses it
if the response is lost. The unique key returns the original row with 200.
A different body with the same `client_id` from the same author is 409 and
does not overwrite. Two authors can use the same `client_id` string. The key
includes `created_by`.

### 11.4 OpenAPI

`scripts/dump_openapi.py` already mounts the store so the chat routes appear
in `openapi.json`. **Exists.** Each new route and event updates that dump in
the same change.

## 12. UI

### 12.1 Web, desktop width

**Exists**, and stays the layout.

- Placement: `ChatHeader`, directly after `PresenceAvatars`. One icon button,
  `aria-label` "Session chat", `aria-expanded` when open.
- Hidden when `access` is `hidden` or the `GET` is 403 or 404. **Adapt** the
  current `shared || items.length` check to `access`.
- Unread dot on the button while closed. **Exists.** The tooltip is the
  body of the last item in the refetch, including the caller's own line if
  that line is last (`SessionChatColumn` sets `tip` from
  `list.items[list.items.length - 1]`). Phase 7 clears the tooltip when
  that last line is deleted. Phase 5 may narrow the tooltip to the latest
  line the server still counts as unread. Until then, do not describe the
  tooltip as "the latest unread body".
- Panel: header "Session chat", close button, list, composer. Close and
  Escape return focus to the agent composer and drop nothing into it.
- Composer placeholder: "Message the people in this session". No model
  picker. Enter sends, Shift+Enter newline. Sending clears the draft only
  after a 200 or 201. A network failure leaves the draft. **Exists.**
- Read-only replaces the composer. It does not leave a disabled textarea.
  **New**, phase 1. Two sentences, so an admin reading a live room does not
  see a "nobody else is here" apology:
  - Caller row, session no longer multi-person: "Session chat is unavailable
    until someone else is on this session."
  - Admin without a row, session is multi-person: "Read only."
  The list remains in both cases.
- Empty writable column: "Only the people on this session see this. The agent
  does not." **New**, phase 1.
- Rows: author, timestamp, body as plain text (`whitespace-pre-wrap` in a
  text node, which is what the skeleton does). Do not render markdown. Do
  not linkify in the first phases. Linkifying is how a chat line becomes a
  phishing surface inside a trusted app.
- Own rows are not restyled into bubbles. One list, oldest at the top.
  **Exists:** while the column is open, every `items` change scrolls the
  list to the last child, including when the reader has scrolled up.
  Phase 3 **Adapts** that to stick only when the reader is already near the
  bottom.
- The panel does not push the transcript. It is `position: fixed` and already
  offsets for the workspace panel. Keep that, including when the workspace
  rail is open.
- Sub-agent pages use the same component with the child conversation id.
  **Exists.**
- Terminal-first web view: the same header control. Phase 3 adds an e2e that
  the control is present in terminal view and that a sent line does not
  appear in the terminal transcript.
- Canvas: the session header is the same header. The control follows the
  session. No canvas-specific copy. If the compact canvas header drops
  `PresenceAvatars`, it must not drop this control independently. Phase 3
  checks the compact canvas header and records the result. If the compact
  header has no room, the control moves into that header's existing overflow,
  still not into the rail.

### 12.2 What the UI must not become

- A `RightRailTab`. The rail's `sidechat` is an agent conversation. A
  `people` tab, including the uncommitted experiment in the session-chat
  worktree, splits "who is here" from the button that must still work when
  nobody is here. Rejected.
- A mode of `CommentsPanel`.
- An overlay on the agent composer.
- A second transcript that shares the composer component. Sharing the
  component is how a human line gets submitted as a turn.

### 12.3 Accessibility

**Exists** in part: button name, expanded state, list, `<time>`.

Phase 3 adds a polite live region inside the open column that announces
"New message from Anna" when a line arrives. It does not move focus. The
unread button exposes `aria-describedby` or an accessible name that includes
"unread" when the dot is set, because the dot is `aria-hidden`.

### 12.4 Display names

**Exists:** raw `created_by`. Phase 3 may apply the same initials and color
`PresenceAvatars` uses (`userInitials`, `userColor`). A friendly name waits
on the accounts directory. Do not block the channel on it. Never let the
client replace `created_by` with a typed nickname in the payload.

### 12.5 Mobile

Phase 11, **New**. Until then the control stays unmounted on a narrow
viewport, which is the skeleton. **Exists.**

When the phase lands: the session-actions menu gains "Session chat" when
`access` is `read` or `write`. It opens a sheet with the same list and the
same composer rules. The sheet is not the agent composer. Unread is a dot on
that menu item. The desktop column code moves into a shared body component
so the two shells do not grow two POST implementations. The sheet does not
ship in phases 1–3, and the menu does not gain a disabled stub before then.

### 12.6 Terminal and TUI

Three different surfaces wear the word "terminal".

1. **Web terminal view** for a terminal-first session (`ViewModeToggle`).
   The human-chat control is in the web header. Section 12.1. The PTY is the
   agent's. Keystrokes there are instructions.
2. **Native harness TUIs** launched by Omnigent (Claude, Codex, Pi,
   Antigravity, and others). They read stdin as the user prompt. This spec
   does not inject lines into that stdin, does not add a slash command that
   the harness might interpret, and does not mirror the thread into the PTY
   scrollback. A person who is only looking at the raw TUI does not have
   human chat until phase 12.
3. **Phase 12, New.** `omnigent session chat --id <id>` is a small client
   of the HTTP API. It prints the `GET` page, reads stdin line by line, and
   `POST`s each line. It opens no harness and writes no PTY. `--id` may be a
   child. Access errors print the same 403/404 the API returned and exit
   non-zero. A follow mode uses the SSE stream only as a wake-up, then
   `GET ?after=`. It does not trust the event for the body, because the
   event has none. This client is a convenience for someone on a machine
   without the web UI. It is not a second product. Estimate assumes a single
   prompt loop, not a full TUI toolkit.

Electron and the web app are the same UI. No Electron-specific chat path.

## 13. History, persistence, export

- Lines live until the root session is deleted, or until a participant
  deletes one line in phase 7. There is no TTL in the default. Section 19
  asks if an operator policy is required. **New** only if the answer is yes.
- Archive does not hide or delete the thread. An archived session the caller
  can still open keeps `access` as computed. **New** as an explicit test.
  The data model already has no archive column, so this is "do nothing",
  verified.
- Deleting a child conversation does not delete the root's lines.
  Deleting the root deletes every `session_chat_messages` row for that
  `root_conversation_id` in the current workspace, and the
  `session_chat_reads` rows once they exist. The mechanics are in section
  3.5: the metadata transaction is best-effort, and a failed second
  transaction can orphan chat rows the same way it can orphan comments.
  Phase 1 accepts that existing tradeoff. It does not invent a new
  cross-store transaction.
- Fork and clone do not copy lines or read cursors. **Exists** by keying on
  the root id. Add a test so a later "copy the session" helper does not
  start copying this table by accident. Phase 9.
- Transcript export (`omnigent session export`, header "export" in
  `exportSessionTranscript`) stays items plus `session_meta`. **Exists.**
  Do not add a `record_type` for chat there. `session import` would turn it
  into agent context.
- Phase 9 export is `GET /chat/export`, JSONL, one object per line:
  `id`, `created_by`, `created_at`, `body`, `edited_at`, `deleted_at`.
  Deleted lines export `body` as `""` and `deleted_at` set. The file is not
  accepted by `session import`. The filename offered by the web menu is
  `<session-title>-chat.jsonl`, from a separate menu item "Export session
  chat" that is shown only when `access` is `read` or `write`.
- Backups of the database contain the bodies. That is true of the transcript
  already. Document it in the spec's security section and do not build a
  second encryption story for one table.
- Workspace id is part of the key. A row is invisible across workspaces the
  same way conversation rows are. **Exists.**

## 14. Privacy and security

- Authorization is the participant rule in section 5, checked on every read
  and every write. The UI hide is not the control. **Exists** for the public
  versus direct-grant split. **Adapt** so a write requires `multi_person`
  and a caller row (section 5.1), which is a stricter rule than `shared`.
- 404 versus 403 matches the session routes: no access hides the session,
  insufficient access is 403. **Exists.** Flag off uses 404 so a probe does
  not reveal the feature. A public-only caller still receives 403 with the
  skeleton's message, which names human chat. That is the issue #3 contract.
  Goal 4 is about the UI and the stream, not about making that 403
  indistinguishable from a missing route.
- The client does not choose `created_by`. **Exists.**
- Bodies are plain text. The web UI inserts them as text. **Exists.** Keep
  a test that a body of `<script>` and one of a markdown link render as
  those characters.
- SSE and typing and update events omit the body. **Exists** for
  `session.human_chat`. A public viewer's stream drops that exact type
  today. Later types are dropped only after the filter **Adapt** in
  section 11.2.
- Server logs. The route must not log `body` at info or warning. A validation
  failure logs the session id and the reason ("empty", "too long"), not the
  text. Phase 8 reads the route and the store for an accidental log and adds
  a regression if one exists. The skeleton's publish path does not include
  the body in the event, which is the main leak. **Exists.**
- Audit. Phase 8 calls `add_audit_attrs` on post, edit, and delete with the
  session id, the message id, and the action. Not the body. The same helper
  the grant route uses.
- Rate limit. Section 11.1. Slows scripted abuse from a compromised
  participant. It is not a trust boundary between participants. Anyone who
  can post can still post 30 lines a minute.
- No HTML sanitizer is required while the renderer uses a text node. If a
  future phase renders markdown, it goes through the existing transcript
  sanitizer and gets a new security review. This spec does not render
  markdown.
- Retention of deleted text is "gone from the row". Database backups and
  replicas that have not applied the update still have it, the same as an
  edited transcript item. Say that in the operator notes of the phase 7 PR.
  Do not claim a delete is a global wipe.
- The human thread is visible to every participant, including people added
  later. Joining a shared session reveals the existing lines. That matches
  the transcript. The share dialog does not need a new warning in phase 1.
  If Jens wants a warning, it is a sentence in `PermissionsModal` ("People
  you add can read the session chat."), phase 2, one line.
- Admin `read` is a support window onto that same thread. Operators who
  staff admins should know admins can read shared-session chat. Solo
  sessions expose nothing because `access` is `hidden` and `items` is empty.
- `client_id` is an idempotency key, not a secret. It can be the uuid the
  browser generated.

## 15. Moderation, edit, delete

Phase 7, **New**. The skeleton is append-only, and it stays append-only
until this phase.

- Author sends `PATCH` with a new body, same 2000-character rule. `edited_at`
  is set. `created_at` and `id` stay. The event is `updated` / `edited`.
- Author, Manage, or Owner sends `DELETE`. `deleted_at` and `deleted_by` are
  set. `body` becomes `""`. The event is `updated` / `deleted`.
- A deleted line cannot be edited or deleted again. 409.
- The UI shows "Deleted" with the original author and time, and no body.
  Edit on your own non-deleted line is a second textarea in the row, not a
  new composer mode that could be confused with the agent composer.
- There is no undo toast that restores the body. Restoring would require
  keeping the body. Section 10.4 drops it.
- Manage cannot edit someone else's wording. They can delete it. A
  replacement is a new line of their own.
- Admin without a row cannot edit or delete.
- Moderation is not a block list, a report button, or a server-wide mute.
  Those are out of scope. The room is small and the owner can revoke the
  grant, which already removes the person from the session.

## 16. Multi-user consistency

- Two posts are two rows. Order is `(created_at, id)`, not arrival order on
  a given SSE connection. A client that appended locally on 201 and then
  refetches must dedupe by `id`. **Exists.**
- Same microsecond, different ids: both rows survive the cursor. **Exists**,
  with a test.
- The SSE event can arrive before the client's 201, or after, or not at all
  if this process had no subscriber. `GET` is the authority. The event is a
  hint to refetch. **Exists.**
- A client must ignore an event whose `conversation_id` is not the stream it
  is reading. The web client already drops foreign ids. The publisher stamps
  each stream with its own id so the copy that stream receives passes the
  check. **Exists.**
- Edit versus delete race: both are writes of the same row. Last committed
  write wins. If delete commits first, the patch sees `deleted_at` and
  returns 409. If the patch commits first, the delete clears the new body.
  No merge of two bodies. Participants are not co-editing one line.
- Read-cursor race: forward-only upsert. **New**, phase 5.
- Idempotent retry: `client_id`. **New**, phase 8. Until then a lost 201
  followed by a retry can double-post. The column should disable the send
  button while the request is in flight (small **Adapt**, phase 1) so a
  double Enter does not double-post from one browser. That does not cover a
  retry after a timeout. Phase 8 does.
- Process-local bus: two server processes can both accept `POST` (the
  database is the lock) and each will notify only its own streams. The other
  process sees the line on the next `GET` (mount, explicit refetch, or a
  later event). Acceptable, and the same as presence. A future cross-process
  bus is out of scope. If Omnigent is ever run with multiple workers, this
  paragraph is the known gap to revisit.
- Clock: `created_at` comes from the server's `now_epoch_us`, never from the
  client. **Exists.**
- Workspace scope: the store filters `current_workspace_id()`. **Exists.**

## 17. Migration and feature flag

### 17.1 Schema

**Exists:** `sc1a2b3c4d5e_add_session_chat_messages.py`, down revision
`mm1a2b3c4d5e`. It is the single head on this branch. A rebase onto a main
that adds another head must move `down_revision` or insert a merge revision.
Do not edit `sc1a2b3c4d5e` after it has been applied in a shared database.
Later phases add new revisions.

The migration runs whether or not the feature flag is on. An empty table is
harmless. Downgrade drops the table and the index. Downgrade of a database
that has lines deletes them. That is what the skeleton's `downgrade` already
does. Say so in the phase PR that first enables the flag for a real
deployment.

No backfill. There is no historical chat to import.

### 17.2 Flag

**New.** The skeleton is on for every CLI, Docker, and Databricks server
because those entrypoints always pass `chat_store`, and there is no
`Feature` enum entry.

Add `session_human_chat` to `omnigent/server/feature_flags.py` and to the
inventory in `designs/FEATURE_FLAGS.md`.

| Field | Value |
| --- | --- |
| Key | `session_human_chat` |
| Default | Off. `FeatureFlags.enabled_features` does not include it. |
| Owner | Web |
| Review by | `0.20.0`. The repo is `0.18.0.dev0` today. Move the date if the flag is still off at that release, the same way the other rows in `designs/FEATURE_FLAGS.md` work. |
| Frontend | Visible in `GET /v1/info` `features`, like the other flags. The web still gates on `GET /chat`, so a stale bundle after a flag flip hides the control on 404. |

When the flag is off:

- The router stays mounted. `create_app` still receives `chat_store`. Each
  handler reads `request.app.state.feature_flags` (set by `create_app`) and
  returns 404 before it reads or writes the store. A missing `feature_flags`
  attribute is treated as off, so a hand-built app does not accidentally
  enable the channel.
- `routes_events.py` reads the same flag when it computes
  `include_human_chat` and forces that value false when the flag is off.
- `POST` does not write. The stream omits `session.human_chat`.
- The web column is not a second policy implementation. A 404 hides it.
  That path **Exists**.

When the flag is on, section 5 applies.

`tests/server/conftest.py` builds `auth_client` with `chat_store` set and
does not pass `feature_flags`. Most of `test_session_chat.py` uses that
client. Only the single-user test builds its own app. Phase 1 does not flip
the shared factory to default-on. It enables the flag on the app those chat
tests already use (a fixture argument, or an explicit `feature_flags=` on
that client only) and adds one test whose app leaves the flag off and
expects 404 with no row written. The e2e server sets
`OMNIGENT_FEATURES=session_human_chat`. A suite-wide default-on would hide
the 404 regression.

Fork deployments that want the channel set
`OMNIGENT_FEATURES=session_human_chat` (comma-separated with whatever else
they enable). Upstream stays dark until someone turns it on. That is the
rollout in `designs/FEATURE_FLAGS.md`: dark image, one deployment, check
`GET /v1/info`, reload, then widen. Rollback is removing the key. Rows
already stored remain in the table and become reachable again when the key
returns. They are not deleted on rollback.

PR #4 does not have to grow the flag before it merges, if the merge target is
the fork and the fork wants the skeleton immediately. This spec's phase 1 is
the flag plus the activation rule, and it should land before the channel is
offered on a multi-tenant server. Section 19 asks which of those two orders
Jens wants.

### 17.3 Accounts dependency

Chat's use of `principals()` is correct only after the resolver is installed.
On the chat branch, a grant written on a linked Google email does not match
the username until PR #5 is merged. The tests fake the resolver. Do not
duplicate `extra_principals` into the chat branch.

Display names, account deletion, and what happens to `created_by` when an
account is removed belong to the accounts work. This spec keeps `created_by`
as an opaque principal string. If account deletion must scrub chat bodies,
that is an accounts-revocation item (`designs/ACCOUNT_REVOCATION.md` is the
place to check when phase 7 is built), not a second implementation here.
Until that is read and matched, deleting an account does not automatically
delete chat lines. Call that out in the phase 7 PR so it is a choice.

## 18. Test strategy

What already exists is listed in section 3.6. The additions below are the
contract for each phase. Each phase updates the tests it supersedes in the
same change.

### Phase 1

- Server: solo owner `POST` of a valid body is 403 and writes nothing. An
  empty body is still 422, because the model validates first. Solo owner
  with an existing row `GET`s `access: "read"` and `POST`s 403. Two grants
  that cluster as two people: `write` and 201. The owner's username plus
  their own linked email, and nobody else, is one cluster: not `write`.
  `__public__` still does not create a cluster or flip `access`. Admin on a
  multi-person session gets `read` and 403 on `POST`. Admin on a solo
  session gets `hidden` and empty `items` even when `shared` is true and
  even when lines exist. Single-user `POST` is 403 and `GET` is `hidden`
  with empty `items`. Flag off is 404, writes nothing, and the stream omits
  the event.
- Paging: `tail=1&limit=100` returns the newest 100 in ascending order and
  sets `has_older` when older rows exist. `before` returns the previous
  page. `after` returns newer rows and does not skip a same-microsecond
  pair. `before` together with `after` is 422.
- Delete: deleting the root removes chat rows. Deleting a child leaves them.
- Web: hidden when `access` is `hidden`. On `read`, the composer is gone and
  the matching sentence from section 12.1 is shown. The first fetch uses
  `tail=1`. An event refetches with `after` set to the last held id. Enter
  still posts only to `/chat`. The in-flight send does not double-post.
- E2e: the existing two-browser journey stays. Add the negative: an unshared
  session has no control.

### Phase 2

- Read grant can post. Read grant cannot reach the agent handoff control
  (assert the control is absent; the handoff itself is phase 10).
- `SharingMode=off` with two pre-existing grants still allows chat.
  `SharingMode=off` with one grant hides it.
- Public visitor plus a real participant: visitor 403, participant 201,
  visitor's stream has no event.

### Phase 3

- Column header lists another viewer and shows the empty-room sentence when
  `viewers` is only the caller.
- The control remains mounted when `viewers` becomes empty.
- Terminal-first: the control is in the header, and the line is absent from
  the terminal transcript.
- Live region announces a new line without moving focus.
- Compact canvas header: the check from section 12.1, asserted one way or
  the other so it cannot regress silently.

### Phase 4

- Typing publish reaches the other participant's stream and not the public
  stream. No row is written. A burst past one per second does not multiply
  events. The indicator clears on `active: false` and after the client
  timeout.

### Phase 5

- Cursor moves forward only. `unread_count` is right for a second client.
  A public caller does not receive `readers`. Opening the column posts the
  latest id.

### Phase 6

- A backgrounded window notifies with the generic body. A focused window
  does not. The caller's own post does not notify. The agent-idle
  notification still fires on a turn end and does not fire on a chat post.
  The dock badge does not change because of a chat line.

### Phase 7

- Author can edit and delete. Edit by someone else is 403. Delete by Manage
  clears the body. A following `GET` returns `body: ""` and `deleted_at`.
  The stream event has no body. The transcript still has no line.

### Phase 8

- Replay with the same `client_id` and body returns the same id and does not
  insert. A different body is 409. Prompt-assembly test from section 6.1.
  Audit attrs present, body absent from the log capture. 31st post in a
  minute is 429.

### Phase 9

- Export JSONL contains the lines and is rejected by session import, or is
  simply not a file that import recognizes. Assert import does not grow
  conversation items from that file. Fork has zero chat rows. Transcript
  export has no chat body.

### Phase 10

- Confirm sends one conversation item and sets `handed_off_item_id`. Cancel
  sends nothing. Read grant has no control. The other human lines are absent
  from the composed prompt. A failed turn leaves the human row in place and
  leaves `handed_off_item_id` null.

### Phase 11 and 12

- Mobile: the menu item is absent on a solo session and opens a sheet that
  posts to `/chat` on a shared one. Desktop column still works.
- CLI: `omnigent session chat` against a test server posts and prints a
  refetched line, and does not spawn a harness. Covered with the CLI's
  existing process test style, not a PTY harness test.

E2e stays in `tests/e2e_ui/collaboration/`. Server tests stay in
`tests/server/test_session_chat.py`. Do not assert chat behavior through the
agent's tool bridge.

## 19. Open questions

These are for Jens. The spec's default is the recommendation. Phase 1 should
not start until question 1 is answered, because it changes the skeleton's
acceptance tests.

1. **Who counts as "in the session"?** Recommendation: two direct grants,
   online or not. The alternative is "two open presence connections". That
   alternative hides the thread whenever Anna closes her laptop, which breaks
   UC2. Confirm the recommendation.
2. **After the last other person is removed, may the owner still read?**
   Recommendation: yes, read-only, until he deletes the session or, in phase
   7, deletes the lines. The alternative is to hide immediately and leave the
   rows only in the database.
3. **May a Read participant post?** Recommendation: yes. They still cannot
   instruct the agent. The alternative is to require Edit to post, which
   leaves a reviewer with no human channel except the composer they are not
   allowed to use.
4. **Admin post.** Recommendation: no. Admin may read a multi-person thread.
   Confirm.
5. **Flag versus landing PR #4 as always-on.** Recommendation: merge the
   skeleton if you want it on the fork now, and make phase 1 the flag plus
   the activation rule before any other chat work. The alternative is to put
   the flag into PR #4 before merge, default off, and enable it in the fork's
   environment.
6. **PR #5 order.** Recommendation: land accounts (`principals` resolver)
   before relying on Google-email grants in production chat. The chat code
   already calls `principals()`. No chat rewrite is required when the
   resolver appears.
7. **OS notification body.** Recommendation: generic text, no message body.
   The alternative is the line itself, which is what the in-app tooltip
   already shows for someone at the machine.
8. **Deleted text.** Recommendation: clear `body` and keep a tombstone.
   The alternative is to retain the old body for abuse review. That needs a
   stated retention and an access rule for who can read it.
9. **TTL.** Recommendation: none. Lines die with the root session. The
   alternative is an operator-configured retention. Not built unless you ask.
10. **Uncommitted `people` rail in the session-chat worktree.** Recommendation:
    leave it uncommitted and do not take it. It mixes a rail tab the ticket
    rejected with unrelated side-chat list filtering. Confirm you want that
    worktree left alone.
11. **Account deletion.** Recommendation: do not scrub chat inside the chat
    phases. Match `designs/ACCOUNT_REVOCATION.md` when phase 7 is built, as
    a separate decision. Confirm that delay is acceptable.
12. **`@agent` in the composer.** Recommendation: never magic. Phase 10 is a
    button with a confirm step, and it can wait until the coordination
    phases (1–7) are real. Confirm it is allowed to wait.

## 20. Phases and effort

Estimates are person-days for one engineer who already knows this repository,
including tests and the OpenAPI dump, excluding review wait and excluding a
rebase onto a moving upstream. The skeleton is done.

| Phase | Work | Kind | Days |
| --- | --- | --- | --- |
| 0 | Walking skeleton, PR #4 | **Exists** | Done |
| 1 | `access` field, write requires multi-person, hide single-user, read-only history, flag, `GET` limit, root-delete cleanup, disable double submit | **Adapt** | 3 |
| 2 | Role matrix, sharing-mode behavior, tests. The share-dialog sentence in section 14 is included only if Jens asks for it | **Adapt** | 2 |
| 3 | Presence line in the column, empty copy, live region, terminal-first and canvas checks, initials | **New** | 2 |
| 4 | Typing route, event, indicator | **New** | 3 |
| 5 | Read cursor, `unread_count`, "Seen by" | **New** | 4 |
| 6 | OS notification, separate from agent-idle | **New** | 3 |
| 7 | Edit, delete, tombstone, manage delete | **New** | 4 |
| 8 | `client_id`, rate limit, audit, prompt-isolation test, NUL reject | **New** | 2 |
| 9 | Chat export, fork/export regression tests | **New** | 2 |
| 10 | "Ask the agent" with confirm | **New** | 5 |
| 11 | Mobile sheet | **New** | 4 |
| 12 | `omnigent session chat` HTTP client | **New** | 6 |

A usable shared-session channel, without typing, receipts, edit, or
notifications beyond the dot that already exists, is phase 1 through 3:
about **7 days** after the skeleton. That is the version that matches the
rule in section 1.

The coordination product (typing, unread that survives a second browser,
notifications, edit and delete, idempotency) is phases 1 through 9: about
**25 days**.

Phase 10 is the risky one. It is the only phase that lets a human line
become an instruction, and only after a confirm step. It should not be
pulled forward to make the channel "feel smart".

Phases 11 and 12 are clients of a finished API. They can trail.

Suggested order if the goal is the smallest thing that obeys section 1:
phase 1, then 2, then 8's prompt-isolation test on its own (half a day,
pulled forward), then 3. Typing and read cursors wait until someone is
actually missing them.

## 21. File map

| Path | Role | Mark |
| --- | --- | --- |
| `omnigent/db/db_models.py` `SqlSessionChatMessage` | Row | **Exists**, later columns **New** |
| `omnigent/db/migrations/versions/sc1a2b3c4d5e_add_session_chat_messages.py` | Schema | **Exists** |
| `omnigent/stores/session_chat_store/` | Append and list | **Exists**, limit and soft-delete **Adapt** |
| `omnigent/server/session_chat.py` | Allow, shared, publish | **Exists**, write rule **Adapt** |
| `omnigent/server/routes/session_chat.py` | HTTP | **Exists**, new routes **New** |
| `omnigent/server/principals.py` | Identity set | **Exists**, keep one copy with PR #5 |
| `omnigent/server/routes/sessions/routes_events.py` | `include_human_chat` | **Exists** |
| `omnigent/server/routes/_sessions/helpers.py` `_stream_live_events` | Drop the event | **Exists** |
| `omnigent/server/schemas.py` `SessionHumanChatEvent` | Union member | **Exists**, new members **New** |
| `omnigent/runtime/session_stream.py` | In-process bus | **Exists**, unchanged |
| `omnigent/server/presence.py` | Viewers | **Exists**, unchanged |
| `omnigent/server/feature_flags.py` | Flag | **New** |
| `omnigent/cli.py`, `deploy/docker/entrypoint.py`, `deploy/databricks/src/app.py` | Construct the store | **Exists**. Flag is enforced in the route, so these can keep constructing it |
| `omnigent/stores/conversation_store/sqlalchemy_store.py` `delete_conversation` | Does not delete chat rows | **Adapt** |
| `omnigent/runtime/prompt.py` | Must keep ignoring the table | **Exists** as ignorance. Test is **New** |
| `web/src/shell/SessionChatColumn.tsx` | Column | **Exists**, `access` **Adapt** |
| `web/src/shell/ChatHeader.tsx` | Placement | **Exists** |
| `web/src/components/PresenceAvatars.tsx` | Avatars | **Exists**, untouched |
| `web/src/lib/humanChat.ts`, `sse.ts`, `events.ts`, `blockStream.ts` | Wake-up path | **Exists** |
| `web/src/components/PermissionsModal.tsx` | Share session | **Exists**. Optional one-line warning **New** |
| `web/src/hooks/useIdleNotifications.ts` | Agent cues | **Exists**. Chat must not call it for lines |
| `tests/server/test_session_chat.py` | Server contract | **Exists**, updated per phase |
| `web/src/shell/SessionChatColumn.test.tsx` | Column contract | **Exists**, updated per phase |
| `tests/e2e_ui/collaboration/test_session_human_chat.py` | Two-browser journey | **Exists** |

## 22. How to read this against the skeleton

If you only read one page: the skeleton already stores a human line on the
root, refuses the public link, keeps the text off the SSE event, and shows a
column beside the presence avatars. The product rule it does not enforce yet
is the one in section 1. A solo session can still `POST`, and a stored line
keeps the control on screen with a live composer. Phase 1 is that change,
plus a flag, a page limit, and deleting the rows with the session. Everything
else in this document is a later phase or an explicit non-goal.
