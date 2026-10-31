# Omnigent Chat test instance

This is the manual test guide for the `epic/team-chat` test phase. The epic
stays off `main` until the chat is cleanly usable and the Playwright suites
are green.

The instance prepared on 31 Oct 2026 runs commit `0ff489ba3` (epic tip after
PR #145). It is a separate process. It does not use port 6767, the production
database, or `~/.omnigent`.

## Open it

On this Mac:

- App: <http://127.0.0.1:6792>
- Same app through a Cloudflare quick tunnel:
  <https://marina-shepherd-comics-responded.trycloudflare.com>

The tunnel URL belongs to this process. It changes when the tunnel is
started again. The ngrok hostname `jholze-omnigent.ngrok-free.app` stays on
the production server at port 6767. Do not point it at this instance.

An older demo is still listening on port 6791. That build is from 22 Oct
(`team-chat-t110`, `f9e892d64`). Use 6792.

## Sign in

Three password accounts: `jens` (admin), `anna`, and `bob`.

The passwords are in
`/Users/jholze/.local/share/omnigent-team-chat-r1-20261031/credentials`
(mode 600). They are not in git.

Use a private window or a second browser profile to sit in the chat as two
people at once.

## What is already there

The T-133 demo seed loaded this data:

- `#general` points at the other channels.
- `#produkt`, `#design`, and `#betrieb` each have messages and one human
  thread.
- One direct message between Jens and Anna.
- In `#produkt`, an agent thread with one recorded agent reply and an open
  approval card (`Genehmigen` / `Ablehnen`). The card stays up while
  `hold.pid` in the data directory is running. Answering the card ends that
  hold.

## What to try

1. Sign in as Jens. Open **Chat**. Confirm `#general`, `#produkt`,
   `#design`, `#betrieb`, and the direct message are listed.
2. Open `#produkt`. Read the thread. Open the agent thread and look at the
   status chip and the approval card on a desktop window and on a narrow
   phone-sized window.
3. Post a new line in `#design` as Jens. Sign in as Anna and confirm the
   line is there. Reply inside the existing thread.
4. Open the direct message as both people and exchange one line.
5. As Jens, approve or reject the card in the `#produkt` agent thread.
   Reload and confirm the card does not stay pending.
6. Sign in as Bob and confirm he can read `#produkt` and the agent thread.

## Start and stop

Data directory:
`/Users/jholze/.local/share/omnigent-team-chat-r1-20261031`

The server, the approval hold, and the tunnel were started from that
directory. Stop all three with:

```bash
/Users/jholze/.local/share/omnigent-team-chat-r1-20261031/stop.sh
```

Start the server again, in the foreground, with:

```bash
/Users/jholze/.local/share/omnigent-team-chat-r1-20261031/start.sh
```

`start.sh` only starts the server. The seed is already in `demo.db`. Repeat
the seed only when you want a fresh approval hold:

```bash
cd /tmp
set -a && source /Users/jholze/.local/share/omnigent-team-chat-r1-20261031/server.env && set +a
/Users/jholze/dev/omnigent-src/.work/team-chat-r1-instance/.venv/bin/python -P \
  /Users/jholze/dev/omnigent-src/.work/team-chat-demo-seed/scripts/seed_team_chat_demo.py \
  --base-url http://127.0.0.1:6792 \
  --credentials /Users/jholze/.local/share/omnigent-team-chat-r1-20261031/credentials \
  --database-uri sqlite:////Users/jholze/.local/share/omnigent-team-chat-r1-20261031/demo.db \
  --hold
```

Code for this instance is the detached worktree
`/Users/jholze/dev/omnigent-src/.work/team-chat-r1-instance` at `0ff489ba3`.

## Known gaps

- This build does not include PR #143 (listed-channel badges updating
  without a bootstrap refetch). That PR is open. Its required checks are
  green. Its Playwright job is still red. See the test status below.
- A new `@agent` mention does not run a live model here. The only agent
  thread is the seeded reply plus the held approval. There is no runner
  and no provider key in this instance.
- Phone agent-account buttons are 32px. NFR-032 asks for 44px. The e2e
  test is an expected failure.
- Forwarding a chat reply into a linked agent session has no runner relay
  in the e2e fixture. That test is an expected failure.
- T-108 (webhook wake) is not in this build. It waits on Q-14 and is R1.1.
- Quick-tunnel links have no uptime guarantee. Prefer
  <http://127.0.0.1:6792> on this Mac.

## Test status (31 Oct 2026)

Fork CI for PR #143 at `6784176b8`:

| Check | Result |
| --- | --- |
| Python lint | pass |
| Pytest (SQLite) | pass |
| Web (lint, typecheck, vitest, build) | pass |
| Team-chat Playwright | 135 passed, 2 xfailed |
| Other UI Playwright | failed, then stopped at `--maxfail=4` |

The four failures before the stop were:

- `tests/e2e_ui/chat/test_claude_native_slow_ready_first_prompt.py`
- `tests/e2e_ui/chat/test_dictation.py::test_transcript_lands_at_the_caret`
- `tests/e2e_ui/files/test_file_autosave.py::test_non_markdown_edit_autosaves`
- `tests/e2e_ui/hotkeys/test_sidebar_hotkeys.py::test_numeric_chord_jumps_to_pinned_session`

The team-chat badge journey
`test_live_post_and_mention_raise_sidebar_badges_without_bootstrap_refetch`
passed on that run. Epic `0ff489ba3` has not had its own Playwright run
recorded here.
