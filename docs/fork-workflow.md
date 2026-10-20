# Fork workflow (jholze/omnigent)

How changes reach the fork's `main` and the local server on Jens's Mac.
`origin` is the fork. `upstream` (omnigent-ai/omnigent) is fetch-only:
its push URL is set to `DISABLED_no_push_to_upstream`. Nothing is ever pushed
upstream.

## 1. Upstream sync

`scripts/fork/upstream-sync.sh` merges `upstream/main` into a
`sync/upstream-YYYYMMDD` branch cut from `origin/main`, pushes that branch to
`origin` and opens a PR. It never merges and never touches `main`.

| Exit | Meaning |
|---|---|
| 0 | PR opened, or `main` already contains `upstream/main` |
| 2 | Merge conflict. Files are listed; nothing is pushed |
| 3 | An open `sync/upstream-*` PR exists. Finish it first |

`--dry-run` does the merge locally and reports conflicts and alembic heads
without pushing.

Conflicts are resolved by hand (or by a PM HUB coder) on a
`sync/upstream-*` branch, then the PR is opened. Rules for a resolution:

- Fork feature flags keep their fork defaults (`team_chat` stays off).
- The alembic chain must end in one head. Add a merge migration if upstream
  added its own head.
- `openapi.json` and `pnpm-lock.yaml` are regenerated, not hand-merged
  (`uv run python scripts/dump_openapi.py`, `pnpm install`).

Schedule: weekly is enough while the fork carries its own features. Run it
from a launchd agent at 05:00 (template below) or let PM HUB run it on its
daily loop (`mkdir -p ~/.omnigent/logs` first for the launchd log). On a
conflict the script removes its scratch branch; recreate `sync/upstream-*`
by hand to resolve. The sync PR goes through the same review and CI as any PR.

```xml
<!-- ~/Library/LaunchAgents/ai.omnigent.upstream-sync.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>ai.omnigent.upstream-sync</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>/Users/jholze/dev/omnigent-src/scripts/fork/upstream-sync.sh</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>5</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/jholze/.omnigent/logs/upstream-sync.log</string>
  <key>StandardErrorPath</key><string>/Users/jholze/.omnigent/logs/upstream-sync.log</string>
</dict></plist>
```

The old GitHub-hosted `upstream-sync.yml` approach (PR #13) is not used: it
needs `ubuntu-latest` minutes, a PAT secret, and it restarted the Mac server
with `launchctl kickstart -k` while interrupting live sessions.

## 2. PR path

1. Branch from `origin/main`, push to `origin`, open a PR against `main`.
2. Review runs through Omnigent (PM HUB spawns the reviewers): DeepSeek V4
   Pro, then Claude Opus via claude-sdk. Opus has the final word. Review is
   pinned to a commit SHA. A `BLOCK` stops the merge; nits do not. Commits that
   only merge `main` into the branch (plus regenerated `openapi.json`) do not
   need a new review. A risky diff (auth, security, migrations, secrets,
   permissions) and every epic → `main` PR also run `security_auditor`
   before Opus. A confirmed high or critical finding blocks the merge.
   `needs_validation` is a hint in the Opus brief. Opus does not clear
   that block. See `docs/fork/security-auditor.md`.
3. Fork CI (`.github/workflows/fork-ci.yml`, the only enabled workflow) runs on
   the two self-hosted Mac runners. Required: `Python lint (ruff, pyrefly)`,
   `Web (lint, typecheck, vitest, build)`, `Pytest (SQLite)`. `E2E UI` is
   non-blocking. Drafts do not run CI.
4. Merge with a merge commit (`gh pr merge N --merge --match-head-commit SHA`),
   then delete the merged branch.

Merge order:

- Alembic: one head on `main` at all times. A PR with a migration sets
  `down_revision` to the current head. When two PRs add migrations, merge the
  older first and re-chain the other (`tests/db/test_migration_*` check this).
- `openapi.json`: after merging `main` into a branch that touches routes or
  schemas, regenerate it, otherwise `test_openapi_drift` fails.
- Before merging a branch that is behind `main`, either merge `main` into it
  and let CI run again, or run the drift and migration tests on the merged tree
  locally.

## 3. Local deploy

`scripts/fork/deploy-local.sh [SHA]` (default `origin/main`). Check first that
PM HUB and other important sessions are idle: the restart interrupts live
turns.

The script picks the launch layout from
`~/Library/LaunchAgents/ai.omnigent.server.plist`. It does not create that
plist and it does not switch layouts.

**Local mode** (no server plist). One job, `ai.omnigent.host`, runs
`python -m omnigent.host.service_entry --local` and that process spawns the
server. Restart is unchanged: `omnigent server stop`, then wait for launchd
(KeepAlive) to start the host again. Do not `launchctl kickstart` right after
the stop: that started a second server. The script kickstarts the host once,
without `-k`, only if no server process came back within 180 s.

**Server mode** (server plist present). Two launchd jobs:

| Job | Program |
|---|---|
| `ai.omnigent.server` | `python -P -m omnigent.cli server --host 127.0.0.1 --port 6767 … --no-open` (KeepAlive true) |
| `ai.omnigent.host` | `python -m omnigent.host.service_entry --server http://127.0.0.1:6767` |

Restart is one `launchctl kickstart -k gui/$(id -u)/ai.omnigent.server`, then
a wait of at most 180 s until exactly one
`omnigent.cli server --host 127.0.0.1 --port 6767` process is running, that
process is not one that was already running before the kickstart, and
`/health` is 200. Then one
`launchctl kickstart -k gui/$(id -u)/ai.omnigent.host`, and a check that
exactly one `omnigent.host.service_entry --server` is running and that the
host pids from before that kickstart are gone. If a job is already coming
up, the script does not kickstart it again.

Steps, in both modes:

1. Builds the three wheels at SHA in `~/dev/omnigent-deploy` (detached) into
   `~/omnigent-backups/wheels-<sha>` with `OMNIGENT_BUILD_WEB_UI=1` (otherwise
   setup.py reuses an old `static/web-ui` bundle). These directories are also
   the build rollback set; keep at least the last two.
2. Backs up launchd plists to `plist-<ts>/` and the DB with `sqlite3 .backup`
   to `db-<ts>/`. Both directories are `chmod 700`. The plists contain
   secrets: copy, never print. Local mode backs up `ai.omnigent.host.plist`.
   Server mode backs up that file and `ai.omnigent.server.plist`. A plist
   backup taken in server mode holds the `--server` host job and is not a
   `--rollback-mode` source.
3. `uv tool install --reinstall --find-links wheels-<sha> 'omnigent[dictation] @ …whl'`.
4. Restarts the active mode as above.
5. Verifies: exactly one `omnigent.cli server --port 6767` process, `/health`
   200, `alembic_version` equals the code head (the server migrates on
   start), session count not lower, `omnigent --version` shows the SHA. Server
   mode also requires `/v1/info` `single_user` false and exactly one
   `service_entry --server`.

Switching into server mode is a plist edit, done by hand from the saved host
plist (copy values, never print them). Do not use `omnigent host enable` for
that switch: it writes the daemon environment from the current shell and
drops variables that exist only in the installed plist (for example the host
OAuth token). After the switch, move `~/.omnigent/local_server.*` aside into
the same backup directory. If those files stay in place,
`ensure_local_omnigent_server` can start a second server on the same
`chat.db`.

Build rollback (either mode): `scripts/fork/deploy-local.sh --rollback <old-sha>`
reinstalls from `wheels-<old-sha>` and restarts the mode that is active now.
A migration that already ran is not undone by that; if the old code cannot
read the new schema, stop the server and restore `db-<ts>/chat.db` first.

Layout rollback (server mode back to local mode):
`scripts/fork/deploy-local.sh --rollback-mode <backup-dir>`. The directory
must already contain `ai.omnigent.host.plist` from before the switch (the
`--local` host job: `service_entry` and `--local`). `local_server.*` files
are restored if present. Before it changes anything, the script checks that
the backup host plist contains those two markers and does not print the
file. Deploy backups taken in server mode are not suitable: they hold the
`--server` host plist, and restoring one would leave no server running. The
script then copies the current plists into a new `plist-<ts>/` (`chmod 700`),
so the server-mode layout can be put back by hand. It `launchctl bootout`s
both jobs, restores the backup host plist, copies `local_server.*` back to
`~/.omnigent/` if present, moves `ai.omnigent.server.plist` out of
`~/Library/LaunchAgents`, and `launchctl bootstrap`s only the host so the
host spawns the server again.
