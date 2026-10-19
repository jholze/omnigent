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
   need a new review.
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

1. Builds the three wheels at SHA in `~/dev/omnigent-deploy` (detached) into
   `~/omnigent-backups/wheels-<sha>` with `OMNIGENT_BUILD_WEB_UI=1` (otherwise
   setup.py reuses an old `static/web-ui` bundle). These directories are also the rollback
   set; keep at least the last two.
2. Backs up the launchd plist to `plist-<ts>/` (contains secrets: copy,
   never print) and the DB with `sqlite3 .backup` to `db-<ts>/`.
3. `uv tool install --reinstall --find-links wheels-<sha> 'omnigent[dictation] @ …whl'`.
4. `omnigent server stop`, then waits for launchd (KeepAlive) to start the
   host again. Do not `launchctl kickstart` right after the stop: that started
   a second server. The script kickstarts once only if no server came back
   within 180 s.
5. Verifies: exactly one `omnigent.cli server --port 6767` process, `/health`
   200, `alembic_version` equals the code head (the server migrates on
   start), session count not lower, `omnigent --version` shows the SHA.

Rollback: `scripts/fork/deploy-local.sh --rollback <old-sha>` reinstalls from
`wheels-<old-sha>`. A migration that already ran is not undone by that; if the
old code cannot read the new schema, stop the server and restore
`db-<ts>/chat.db` first.
