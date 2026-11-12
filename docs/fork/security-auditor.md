# Security auditor

Read-only gate for risky pull requests on `jholze/omnigent`. The agent is
`omnigent-security_auditor` (`agents/lead/agents/security_auditor/`). The
checklist is Cloudflare's `security-audit` skill, MIT, pinned in
`agents/lead/agents/security_auditor/VENDOR.md`. The agent prompt narrows
that skill. It does not replace DeepSeek or the Opus code review.

## When it runs

The lead runs it before `omnigent-reviewer` when any of these hold:

- The diff Base..Head touches auth, security, an Alembic or other
  migration, secrets handling, or permissions. A path match is enough.
  Labels `security`, `auth`, and `migration` count as well.
- The pull request targets `main` and the head branch is an epic
  (`epic/*`).

Other pull requests skip it. Profile is always `quick`. Scope is always
that diff. The agent does not execute target code, does not spawn, and
does not use the network. Those limits are policies on the agent, so a
prompt in the diff cannot turn them off.

## What it writes

`<repo>/.work/security-audit-out/<run-id>/findings.json`

`.work/` is gitignored. `/tmp` is outside the session sandbox, so the
agent cannot write there.

`<run-id>` is `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, usually `pr-<number>`.
The file is a JSON array in the skill's `report-schema.json`. The agent
validates it with the vendored `validate-findings.cjs`. An empty array
means the pass found no candidate.

## Gate

| Record | Effect |
|---|---|
| `confirmed` and `severity.overall_severity` is `high` or `critical` | Blocks. No devops, not mergeable. |
| `confirmed` at `medium`, `low`, or `informational` | In the Opus brief. Does not block. |
| `needs_validation` | Hint in the Opus brief. No severity. Does not block. |
| `rejected` | Count only. |

Opus (`omnigent-reviewer`) still writes the closing code verdict and has
the last word on that verdict. Opus does not clear a blocking finding.
A fix-round re-runs this agent on the new SHA. Order when the trigger
matches: the review-matrix first pass, this agent (it may run beside
that first pass), `gpt_review` only when the task is already large, then
Opus with both reports and the findings path.

## Spawn

The lead uses `sys_session_create` with the `agent_id` in
`agents/lead/config.yaml`. After this bundle changes, register it again
and keep that id in the lead table:

```bash
omnigent agent add agents/lead/agents/security_auditor --server http://127.0.0.1:6767
```

Do not point this registration at production and do not restart launchd.

Brief: repo, worktree, branch, pinned SHA, base SHA, head SHA, run id,
and the trigger that fired. The diff command is
`git diff <base-sha>...<head-sha>` (three dots, the pull-request
range). `git -C <worktree>` is allowed. The shell may be the primary
checkout, whose HEAD is not the pull-request head. Give both SHAs.

## Pilot

Five merged pull requests, profile `quick`, three-dot range
`base...head`. Model Claude Opus 5.5, reasoning high. The session
metadata does not include token counts, so the cost column is wall
clock only.

The first pass started at 2026-10-20 00:07 UTC and could not write
`/tmp/omnigent-security-audit/`, which is outside the session sandbox.
Those replies are not in the table. The second pass started at 00:11
UTC, wrote `<repo>/.work/security-audit-out/pr-<n>/findings.json`, and
the vendored validator accepted every file. No `confirmed` finding had
`overall_severity` high or critical, so none of these five would have
blocked the gate.

| PR | Trigger | Wall clock | Validator | Gate | Hits |
|---|---|---|---|---|---|
| [#19](https://github.com/jholze/omnigent/pull/19) | migration | 2.5 min | 2 valid | PASS | 1 needs_validation (identity remap skips the new chat principal columns), 1 rejected |
| [#38](https://github.com/jholze/omnigent/pull/38) | auth | 3 min | 1 valid | PASS | 1 rejected (`single_user` is display-only in this diff) |
| [#14](https://github.com/jholze/omnigent/pull/14) | auth | 3 min | 2 valid | PASS | 2 rejected (forwarded identity header, live-stream subscribe) |
| [#29](https://github.com/jholze/omnigent/pull/29) | auth | 3.5 min | 2 valid | PASS | 1 needs_validation (`ap_native` cookie planted by a cross-site GET), 1 rejected |
| [#24](https://github.com/jholze/omnigent/pull/24) | secrets | 5 min | 4 valid | PASS | 1 confirmed low (redaction misses compound key names), 1 needs_validation (outbox thread drops workspace context), 2 rejected |

`needs_validation` goes to the Opus brief as a hint. The confirmed low
on #24 does not block. A later pilot replaces this table. It does not
copy the findings into the repository. The JSON stays under
`.work/security-audit-out/`, which `.*` in `.gitignore` already ignores.
