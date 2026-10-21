# Agent guidance

Guidance for AI agents (Claude Code, Copilot, Cursor, etc.) working in this
repository. See `CONTRIBUTING.md` for the full contributor workflow.

## Reuse and design

- Search for and reuse existing helpers before adding code. Refactor an existing
  implementation if that avoids duplication.
- Use descriptive names; avoid unexplained abbreviations and single-letter names.

## Test changes

Before adding test setup, read [the test guide](tests/README.md) and
[primitive catalog](tests/_helpers/README.md), then inspect the nearest scenario.
Reuse sufficient coverage unchanged or extend a compatible scenario. Keep input
matrices at the lowest reliable layer; retain E2E for distinct process, transport
or browser boundaries. Reuse primitives with explicit caller inputs and keep
faults and assertions in the test. Explain new setup or a separate E2E in the
PR's Test Plan (Repro uses `evidence`; Resolve uses `test_audit`). Count helper changes in
test LOC; fewer lines alone do not establish adequate coverage.

- Add a unit test for non-trivial functions, methods, and exported APIs. Add
  coverage when changed behavior needs it.
- Run the relevant tests before submitting. Document unrelated pre-existing
  failures in the PR description.

## Committing

Run the `pre-commit` hook before committing (`pre-commit run --all-files`, or
let it run on staged files via `git commit`). Fix any issues it reports so the
commit lands clean — CI runs the same checks.

## Local development shortcuts

Use `just` for common tasks; run `just --list` for grouped recipes.

- `just ensure` — install/check prerequisites
- `just run-ios` / `just run-android` — build/run mobile apps
- `just dev` / `just dev-mobile` — start the omnigent dev pod
- `just electron-dev` / `just electron-build` — Electron desktop shell
- `just lint` / `just lint-all` — run pre-commit
- `just normalize-locks` — rewrite lockfile registries to PyPI/npmjs.org

## Pull requests

When you open a pull request, fill in the repo's PR template at
`.github/pull_request_template.md` (case-sensitive on Linux — note the lowercase
filename). Keep every section and checkbox row so reviewers can skim them,
except the optional Changelog section as described below.

- **Summary** — what changed and why.
- **Test Plan** — how you verified it.
- **Demo** — a **video or images** showing the change. Expected on contributor
  PRs for UI / frontend changes (check the "UI / frontend change" box under
  *Type of change*) so reviewers can see the new behaviour without checking out
  the branch. Use `N/A` for non-visual changes.
- **Type of change** / **Test coverage** — check all that apply (at least one
  each).
- **Coverage notes** — required if you checked "Manual verification completed"
  or "Not applicable".
- **Release notes** — choose exactly one Yes/No checkbox and keep both rows.
  Choose Yes only for outstanding user-facing features, bug fixes, UX changes,
  and breaking changes. Breaking changes must choose Yes. Features behind a
  feature flag are eligible only once the flag is enabled for users. Choose No
  for small fixes or improvements, features behind disabled flags, and internal
  changes with no user impact. This is the author's recommendation for
  maintainers curating the release notes.
- **Changelog** — if Release notes is Yes, write one line describing the change
  for users, including compatibility impact for breaking changes. If No, delete
  this section; the complete changelog still credits the PR using its title.

Generate the description from the actual diff and this session's context — lead
with the motivation, then the change. Don't pass a `--body` that skips these
sections.

### Demo media

Do not commit screenshots or recordings created only as PR or issue evidence.
Upload them as GitHub attachments and embed the attachment URLs in the PR's
Demo section or issue comments. Keep local captures outside the tracked tree,
and redact private data before uploading.

Commit media only when it serves maintained documentation, product assets, or
test baselines, not merely to obtain a public demo URL. If attachment upload is
unavailable, explain the limitation and ask for help instead of committing the
files as a workaround.

## Finishing a task

When you finish a task, print instructions to the user on how to test it: the
commands to run, the inputs to provide, or the steps to reproduce so they can
verify the result themselves. Prefer verification that is best performed by a
human, such as concrete manual behavior checks, rather than only listing unit
test commands. Don't leave the user guessing how to confirm the work — tell
them exactly what to do.

To drive the app yourself and prove a user-facing change on every entry point a
user can reach, start with `feature-map/README.md`, then read the relevant maps and linked skills.

## Deprecating features

When deprecating a feature, note the version in which it is expected to be
removed so we can clean it up when that version ships. Call out the deprecation
version in code (e.g. a `@deprecated` tag or comment naming the target release)
and in the PR/commit description, so there's a clear marker to act on later.

## Code comments

Keep comments short and focused on the code, not on the change history.

- **Keep them brief** — prefer one or two lines. Avoid comments longer than
  three lines; if you need more, the code likely needs refactoring or a doc
  string, not a wall of inline commentary.
- **Describe the scenario, not the PR** — explain *what* the code handles or
  *why* it exists, in terms a future reader needs. Don't reference PR numbers,
  issue numbers, or ticket IDs (e.g. `#1646`, `fixes JIRA-123`); the scenario
  should be clear without chasing external links.

## Database query names

Application stores use `make_named_managed_session_maker` and give every
session a stable semantic operation name. The session-level name must describe
the caller's intent rather than repeat SQL syntax; use a nested
`query_name_scope` only when one transaction needs distinct names for important
subqueries. Because the named session covers implicit flush and commit, don't
add an explicit `flush()` only to make a query name observable.

## Framework-owned instructions

Keep runtime lifecycle and metadata instructions separate from portable agent
instructions:

- Agent-spec and per-request instructions are user-authored. Framework-owned
  instructions are additive runtime behavior and are appended after them in
  `omnigent/runtime/prompt.py`.
- Keep the canonical instruction text and lifecycle gate in the owning framework
  module. Harness adapters should only transport the composed instructions; do
  not duplicate policy across adapters or add lifecycle metadata to `AgentSpec`.
- If framework instructions grow beyond a small ordered list, introduce a
  structured `FrameworkInstructions` value at the prompt-composition boundary.

## Omnigent project rules

Fork-only section; keep it when syncing from upstream. Short summary of the
standing rules (Jens, 2026-10-20).

1. **Mobile is mandatory** (NFR-032 in
   `specs/omnigent-chat/04-requirements.md` v0.7). Every new or changed UI is
   built and tested for desktop and phone in the same ticket, never split into
   a follow-up ticket and not asked about per ticket. Viewports 1280x800 and
   390x844. No horizontal scrolling, touch targets at least 44px, long names
   wrap. A missing viewport run is not a pass.
2. **Back channel.** The session that owns the ticket (the lead) reports every
   finished step, blockers and needed decisions to Spec (Jens' orchestrator)
   by running
   `~/.config/grokbot/notify-spec.sh "<session-id>" "<ticket>" "<message>"`.
   The session that opens the PR sends the PR-open notice. Sub-agents call the
   script only when their brief says so or they are blocked; otherwise they
   put blockers in their result for the lead. Read-only reviewers
   (`omnigent-reviewer`, `ux-reviewer`) do not run the script; they put
   findings and blockers in their result. Do not poll; do not expect Spec to
   poll.
3. **Project separation.** Work for one project (e.g. ProductOS) never goes
   into another project's Omnigent project or sessions; this repo and its
   sessions are Omnigent only.
4. **Existing standards.** Review follows the lead matrix in
   `agents/lead/config.yaml`: DeepSeek first, then Opus (final verdict).
   DeepSeek does not review a DeepSeek-written diff. Large tasks add
   `gpt_review` after the first pass and before Opus. No merge without green
   CI and Jens' OK (team-chat task PRs go to `epic/team-chat`; the epic
   reaches `main` only when Jens calls it release-ready). The test instance
   stays separate from production (own port, own DB, production untouched).
   CI runs only on self-hosted runners.

## Local orchestration (jholze fork)

Fork-only section; keep it when syncing from upstream.

Leads follow agents/lead/config.yaml (R1–R4). Spawn helpers only via
sys_session_create with an agent_id from that file. Pin the commit SHA in
every brief and read results with sys_read_inbox.
