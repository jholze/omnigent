# Team Chat R1 launch (T-123) — Runbook

Source: `specs/omnigent-chat/07-tasks.md` — T-123 · Release R1 · wave 12
Implements: NFR-022 (one Alembic head, migrations run), NFR-025 (flag off
loses no data; flag on again shows it).
Depends on: T-120, T-121, T-122, T-129.

Jens runs every production step himself. Agents do not deploy, do not
restart running Omnigent sessions, and do not migrate production data or a
copy of it.

**A failed check in any step stops the launch.** Do not continue to the
next step. Write down what failed and go to [Rollback](#7-rollback) if
production was already changed.

Placeholders used below:

| Placeholder | Meaning |
| --- | --- |
| `<SHA>` | The R1 release commit on `jholze/omnigent` (epic/team-chat merged to `main`). |
| `<TS>` | Timestamp for this run, for example `20261024-0900`. |
| `<HOME>` | Jens' home directory as an absolute path, for example `/Users/jholze`. |

Production layout (see `docs/fork-workflow.md` and `docs/DATA_DIR_LAYOUT.md`):
server `127.0.0.1:6767` under launchd, database `<HOME>/.omnigent/chat.db`,
backups under `<HOME>/omnigent-backups/`.

## 1. Backup

1. Pick a time when no Omnigent session is in the middle of a turn. Later
   steps restart the server; do not restart individual sessions or runners.
2. Create the backup directory and keep it private:

   ```bash
   mkdir -p <HOME>/omnigent-backups/team-chat-r1-<TS>
   chmod 700 <HOME>/omnigent-backups/team-chat-r1-<TS>
   ```

3. Back up the database with SQLite's online backup (safe while the server
   runs):

   ```bash
   sqlite3 <HOME>/.omnigent/chat.db ".backup '<HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db'"
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db "PRAGMA integrity_check;"
   ```

   Expected: `ok`.
4. Copy the launchd plists into the same directory. They hold secrets: copy
   them, never print or paste them.

   ```bash
   cp <HOME>/Library/LaunchAgents/ai.omnigent.host.plist <HOME>/omnigent-backups/team-chat-r1-<TS>/
   cp <HOME>/Library/LaunchAgents/ai.omnigent.server.plist <HOME>/omnigent-backups/team-chat-r1-<TS>/
   ```

   (The second file exists only in server mode.)
5. Record the baseline from the backup:

   ```bash
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db "SELECT version_num FROM alembic_version;"
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db "SELECT count(*) FROM conversations;"
   ```

   Note both values. The session count must not drop in any later step.

Stop if the integrity check is not `ok` or the backup file is missing.

## 2. Install from the jholze/omnigent fork

Install from the fork `jholze/omnigent`, never from `omnigent-ai/omnigent`
or the PyPI wheel. This step prepares the release checkout used by steps 3
and 4. It does not restart production; that happens in step 5.

1. Use the deploy checkout `<HOME>/dev/omnigent-deploy`. Check that it points
   at the fork:

   ```bash
   git -C <HOME>/dev/omnigent-deploy remote get-url origin
   ```

   Expected: a URL containing `jholze/omnigent`. If it shows `omnigent-ai`,
   stop and fix the remote first.
2. Fetch and check out the release commit, detached:

   ```bash
   git -C <HOME>/dev/omnigent-deploy fetch origin
   git -C <HOME>/dev/omnigent-deploy checkout --detach <SHA>
   git -C <HOME>/dev/omnigent-deploy rev-parse HEAD
   ```

   Expected: exactly `<SHA>`.
3. Install the project environment in that checkout:

   ```bash
   cd <HOME>/dev/omnigent-deploy
   uv sync
   ```

Stop if the remote is not the fork or `HEAD` is not `<SHA>`.

## 3. One Alembic head

Still in `<HOME>/dev/omnigent-deploy`:

```bash
uv run --no-sync alembic -c omnigent/db/alembic.ini heads
```

Expected: exactly **one** line, ending in `(head)`. Note that revision id;
it is the target for step 4.

Optional, same checkout, no production data involved:

```bash
uv run --no-sync pytest tests/db -q -m "not databricks"
```

Expected: green. Tests marked `databricks` need the `psycopg` extra and run
only in that CI lane.

Stop if there are two or more heads. That is a release blocker (NFR-022).

## 4. `alembic upgrade head` on a copy

Run the upgrade on a **copy** of the backup from step 1, never on
`<HOME>/.omnigent/chat.db`.

1. Make the copy:

   ```bash
   mkdir -p <HOME>/omnigent-backups/team-chat-r1-<TS>/probe
   cp <HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db <HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db
   ```

2. Upgrade the copy. `OMNIGENT_DB_URL` must be set on the same line; without
   it Alembic falls back to `sqlite:///omnigent.db` in the current
   directory. Use four slashes for an absolute SQLite path.

   ```bash
   cd <HOME>/dev/omnigent-deploy
   OMNIGENT_DB_URL=sqlite:///<HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db uv run --no-sync alembic -c omnigent/db/alembic.ini upgrade head
   OMNIGENT_DB_URL=sqlite:///<HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db uv run --no-sync alembic -c omnigent/db/alembic.ini current
   ```

   Expected: the upgrade ends without an error, and `current` prints the
   single head from step 3.
3. Check the copy:

   ```bash
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db "PRAGMA integrity_check;"
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db "SELECT count(*) FROM conversations;"
   sqlite3 <HOME>/omnigent-backups/team-chat-r1-<TS>/probe/chat.db "SELECT count(*) FROM chat_channels;"
   ```

   Expected: `ok`; session count equal to step 1; the chat tables exist.

Stop if the upgrade errors, `current` is not the head, or the session count
changed.

## 5. Flag `team_chat` on

Production changes start here.

1. Deploy `<SHA>` with the fork deploy script. It builds wheels, backs up the
   DB and plists again, reinstalls, restarts the active launchd mode, and
   checks `alembic_version` against the code head (the server migrates on
   start):

   ```bash
   cd <HOME>/dev/omnigent-deploy
   scripts/fork/deploy-local.sh <SHA>
   ```

   Expected: no `DEPLOY CHECK FAILED`. `omnigent --version` shows `<SHA>`.
2. Check the server with the flag still off:

   ```bash
   curl -s http://127.0.0.1:6767/v1/info
   ```

   Expected: HTTP 200, `features.team_chat` is `false`.
3. Turn the flag on. Set `OMNIGENT_FEATURES` in the `EnvironmentVariables`
   of the plist that starts the server (`ai.omnigent.server.plist` in server
   mode). The value is comma-separated; if the key already lists features,
   keep them and append `team_chat`. Edit without printing the file, for
   example:

   ```bash
   plutil -replace EnvironmentVariables.OMNIGENT_FEATURES -string team_chat <HOME>/Library/LaunchAgents/ai.omnigent.server.plist
   ```

   `launchctl kickstart` reuses the loaded job definition, so reload the job
   once so the new environment applies (`launchctl bootout`, then
   `launchctl bootstrap` of the same plist).
4. Verify:

   ```bash
   curl -s http://127.0.0.1:6767/v1/info
   ```

   Expected: `features.team_chat` is `true`, and exactly one
   `omnigent.cli server --port 6767` process is running.

Stop if the deploy check fails, the server does not come back, or
`features.team_chat` is not `true`.

## 6. Smoke

Run in the production web UI as Jens, with a second person (or a second
browser profile) where noted. All three checks must pass.

1. **Post in #general.** Open Team Chat, open `#general` (create it if it
   does not exist yet), post a short test message. Expected: the message
   appears once, survives a page reload, and appears for the second person
   without a reload.
2. **@agent thread.** In `#general`, post a message that mentions an agent
   (`@` + agent name) with a small task. Expected: a thread opens on that
   message, the agent status chip shows the agent working, and the agent
   reply appears in the thread.
3. **Approval.** In an @agent thread, ask the agent for an action that needs
   approval (for example a shell command). Expected: an approval card
   appears in the thread; approving it lets the agent continue and the
   result appears in the thread.

**If any smoke check fails, the launch stops.** Do not invite the team.
Turn the flag off (step 7.1) and decide on a fix or a full rollback.

Record the result:

| Check | Result | Note |
| --- | --- | --- |
| Post in #general | | |
| @agent thread | | |
| Approval | | |

## 7. Rollback

### 7.1 Flag off, data stays, flag on shows it again (NFR-025)

This is the first rollback and also a launch check. Run it once after a
green smoke.

1. Count the chat data:

   ```bash
   sqlite3 <HOME>/.omnigent/chat.db "SELECT count(*) FROM chat_messages;"
   ```

2. Flag off: remove `team_chat` from `OMNIGENT_FEATURES` in the server plist
   (keep other features), reload the job as in step 5.3.
3. Expected: `/v1/info` shows `features.team_chat` `false`, Team Chat is
   gone from the UI, other Omnigent sessions work as before, and the count
   from 7.1.1 is unchanged.
4. Flag on again as in step 5.3 and 5.4.
5. Expected: `features.team_chat` `true`; `#general`, the smoke messages,
   the @agent thread, and the approval card are visible again; the count is
   unchanged.

If data is missing after flag off or flag on again, the launch stops.

### 7.2 Full rollback (only if the release itself is broken)

1. Flag off as in 7.1.2.
2. Reinstall the previous build:

   ```bash
   scripts/fork/deploy-local.sh --rollback <old-sha>
   ```

3. A migration that already ran is not undone by that. If the old code
   cannot read the new schema, stop the server and restore
   `<HOME>/omnigent-backups/team-chat-r1-<TS>/chat.db` to
   `<HOME>/.omnigent/chat.db`, then start the server again. Chat data
   written after the backup is lost in that case.

## 8. Bot smoke

Bots need a green bot smoke before launch:
[Grok-Bot Client Smoke (T-129)](team-chat-r1-bot-smoke.md).

The run on 2026-10-22 passed with a local MCP client, not the Grok Bot
product. The product-client run is still open there. **Without a green bot
smoke there is no launch go for bots.**

## Launch go

Go only when steps 1 to 7 are green. Go for bots only when step 8 is also
green. Any failed smoke stops the launch.
