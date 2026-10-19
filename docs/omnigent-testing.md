# Test strategy — jholze/omnigent

The product checkout is a worktree under `.work/` (default
`.work/omnigent-app`). Commands below run from that worktree.

`test_author` writes tests. `tester` runs them. Those are two agents.
A run by `test_author` while writing is iteration. The gate is `tester`.

## Layers

| Layer | Where | When it is the gate | Automatic |
|---|---|---|---|
| Server unit | `tests/server/`, `tests/stores/`, `tests/db/` | Every ticket that touches the server | Yes. `pytest` on PR (`ci.yml`). |
| Web component | `web/src/**/*.test.ts(x)` Vitest | Every ticket that touches the widget | Yes. `web-tests.yml` on `web/**`. |
| Browser journey | `tests/e2e_ui/` Playwright Python | When the contract needs a real browser (two users, streams, presence, login) | Yes. `e2e-ui.yml` on every non-draft PR, 10 shards, mock LLM, no secrets. |
| Native / live LLM | `tests/e2e_live/`, parts of `tests/e2e/` | Never a ticket gate | Nightly only. Issue #2 tracks a red nightly. |

Do not add a second Playwright tree under `web/e2e`. ProductOS uses
TypeScript Playwright because that app has no Python UI suite. This
fork already has `tests/e2e_ui`.

## Ticket commands

The issue `## Acceptance` section is the gate. Each line is one command
plus one observable result. `tester` runs those lines and nothing else.

Typical:

```bash
OMNIGENT_SKIP_WEB_UI=true uv run pytest tests/server/test_<feature>.py -q
cd web && pnpm exec vitest run src/<path>.test.tsx
uv run --no-sync pytest tests/e2e_ui/<path> -q
```

One-time for browser tests:

```bash
uv sync --extra all --group test
uv run --no-sync playwright install --with-deps chromium
```

`tests/e2e_ui` starts `omnigent server` itself against
`examples/hello_world.yaml` and a mock LLM. It does not need docker
compose. Iterate against a running SPA only with
`OMNIGENT_E2E_ALLOW_DEV_BASE_URL=1` and `--ui-base-url`.

## What to write per ticket

- New route, store, or permission rule → pytest under `tests/server/`.
- New React widget → Vitest next to the file, same patterns as
  `SessionChatColumn.test.tsx` / `LoginPage.test.tsx`.
- A path two humans take in a browser (share, chat column, Google bind)
  → one focused file under `tests/e2e_ui/`, using fixtures in
  `tests/e2e_ui/conftest.py`. Do not run the whole suite on the ticket.
- Nightly native-CLI failures (issue #2) → reproduce with the workflow
  command from `.github/workflows/e2e-ui.yml` or `e2e.yml`, then a
  regression test in the layer that actually failed.

## ProductOS answers (PM Hub, 2026-10-16)

Asked in session `2d377d57f8644772bca62ddf06b7ae66`. Their app is
different: Playwright TypeScript is the only frontend runner, Vitest
is not installed, and `npm run check` (build) is the merge gate.
Playwright runs only when docker compose is up. We do **not** copy
that stack. We copy these rules:

- One spec per journey. Chromium only on the ticket. Filter a file;
  never put the spec behind a Playwright `dependencies` entry (that
  runs the dependency project unfiltered).
- Auth is per test, not a shared `storageState` file (parallel workers
  race on one user). Login through the API, then seed the session.
  UI login is only for the login spec.
- `webServer` that starts the SPA is not enough if the API is not on
  that process. For us the e2e_ui fixture starts `omnigent server`.
- Teardown may delete only rows it created (prefix guard). Serial
  mode for tests that share a login. Assert a stable heading with a
  long timeout when the page fans out.
- Local retries stay 0. Keep the assertion, raise the timeout.
- For a new chat column they would start with a Playwright journey
  (open, empty state, one row), and a unit test only for a pure
  helper. We already have Vitest, so the widget still gets a
  component test, and the two-browser path gets `tests/e2e_ui`.

## Gate vs claim

`test_author` may run a test to see red or green. `tester` repeats the
named commands and reports `GATE PASS` or `GATE FAIL`. Opus (`reviewer`)
reads those counts. A missing run is a fail.
