# Approval watcher

A small local receiver for the [manager webhook](../../docs/manager-webhook.md).
It listens for `session.awaiting_decision`, checks the signature, and runs a
fail-closed allow-list on the card. It approves a card only when the command
is harmless and local. Every other card is reported to the owner and left
open for a human.

> **Do not point the live server at this receiver.** The server on
> `127.0.0.1:6767` (`~/.omnigent/config.yaml`) keeps `manager_webhook`
> disabled until Jens enables it. Enabling it there would start delivering
> live elicitations. Test against a throwaway Omnigent server that uses a
> temporary config and data dir (see [Try it](#try-it-on-a-throwaway-server)).

## What it does

1. Verifies `X-Omnigent-Signature` with
   `omnigent.server.manager_webhook_signing.verify`. It accepts
   `OMNIGENT_MANAGER_WEBHOOK_SECRET` and, during a rotation,
   `OMNIGENT_MANAGER_WEBHOOK_SECRET_PREVIOUS`. A bad or stale signature gets
   `401`, and nothing is resolved.
2. Replies `204` to every other event type without acting.
3. Dedupes on `event_id`, so a retried delivery is not acted on twice.
4. Runs `policy.evaluate_card` on `data`.
   - **approved**: POSTs `{"action":"accept","content":{...}}` to
     `data.resolve.path` on `OMNIGENT_APPROVAL_WATCHER_SERVER_URL`. The path
     must be this card's `/v1/sessions/<id>/elicitations/<id>/resolve`
     route. `data.resolve.url` is ignored.
   - **reported**: no resolve call. It writes one stdout line and one audit
     line. It never posts into a chat session.
5. Appends one JSONL line per decision: `event_id`, `time` (ISO UTC),
   `epoch`, `occurred_at`, `session_id`, `target_session_id`,
   `elicitation_id`, `policy_name`, `decision`, `command` (redacted),
   `reason` (redacted). `epoch - occurred_at` is the delivery-to-decision lag.
   Count `decision` values over a night to measure auto-approval coverage.

## Allow-list

All rules are in `policy.py`. Tests can call `evaluate_command` and
`evaluate_card` directly, without HTTP. This is an allow-list: a program
that is not named below is reported. The watcher approves only when the
card has a command it can read and every segment of it
(`&&`, `||`, `;`, `|`, `&`, subshells) passes.

- **Readable command.** Today that is a Codex command-approval card
  (`Codex wants to run **<command>**`), or an explicit `command` field. A
  Claude-native `wants to call **Bash**` card does not include the command
  in the webhook payload, so it is reported. Forms with schema fields,
  plan approvals, and unknown cards are also reported.
- **Shell wrapper.** `<shell> -<flags> '<script>'` (sh, bash, zsh, dash,
  ksh) is unwrapped when the flags contain `c` and use only `l c e x u`.
  The script is then checked with the same rules. Codex's real form,
  `/bin/zsh -lc 'ls /tmp'`, therefore approves, and
  `/bin/zsh -lc 'git push'` is reported. A shell run without `-c`, or
  with extra arguments after the script, is reported.
- **Program paths.** A program given by path must live in `/bin`,
  `/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin`, `/sbin`, or
  `/usr/sbin`, and is then judged by its name. `./tool` or `/tmp/zsh` is
  reported.
- **Allowed programs.**
  - Read-only and basic tools: `ls`, `cat`, `head`, `tail`, `wc`, `grep`,
    `rg` (not `--pre`), `find` (not `-delete`, `-exec*`, `-ok*`,
    `-fprint*`, `-fls`), `echo`, `pwd`, `test`, `[`, `diff`, `true`,
    `false`, `basename`, `dirname`, `realpath`.
  - `cd` with exactly one local directory. A bare `cd` or `cd -` is
    reported, because it jumps to `$HOME`/`OLDPWD` and later relative
    paths would no longer be local.
  - `curl` and `wget`, only when every target is loopback.
  - Project tools: `pytest`, `ruff`, `just` (only `-l`, `--list`, `-n`,
    `--dry-run`, `--summary`), `uv run <allowed command>`, and
    `npm`/`pnpm` with `test`, `run`, `run-script`, `ls`, `list`, or `why`.
    `npm test` and `just <recipe>` run the project's own scripts.
  - Project-directory options must stay inside a workspace root; `/tmp`
    does not count here.
    - The options are `npm --prefix`, `pnpm -C`/`--dir`,
      `uv --project`/`--directory`, and `git -C`/`--git-dir=`/
      `--work-tree=`, in both the `--opt value` and `--opt=value` forms.
    - `uv --package` must be a plain package name.
    - `--filter` must not be an absolute path or contain `..`.
    - npm and pnpm read options on both sides of the subcommand, so
      `npm test --prefix /tmp/x` is checked too, up to a literal `--`.
      Anything after `--` goes to the script, not npm. Any other npm/pnpm
      option is reported, `--script-shell` included.
  - Project tools run project code or config from the current directory:
    `npm`, `pnpm`, `uv`, `just`, `pytest`, `ruff`, and `git` (a foreign
    repo's `.git/config` can run programs).
    - They are reported once the directory is outside the workspace roots,
      and here `/tmp` counts as outside.
    - That covers a card `cwd` such as `/tmp/x`, an earlier
      `cd /tmp/x && …`, and a `cd` inside a nested `sh -c` script.
    - `ls` or `cat` in `/tmp` stays approved.
  - pytest:
    - Test paths, `-c`/`--config-file`, `--rootdir`, and `--confcutdir`
      must be inside a workspace root.
    - `-p <plugin>` is reported, but `-p no:<plugin>` is allowed.
    - `-o`/`--override-ini` is reported for `pythonpath`, `testpaths`,
      `rootdir`, `confcutdir`, `addopts`, `required_plugins`, `cache_dir`,
      and `log_file`.
    - Any `@argfile` argument or option value is reported, including a
      workspace-relative one such as `@args.txt`. pytest expands it into
      more arguments, and the watcher cannot read the file.
    - Short flags may only be `q v x s l h`; value options such as `-k`,
      `-m`, `-r`, `-n`, and `-W` work in both the separate and attached
      forms. Any other short option is reported.
    - `--basetemp` is always reported, because pytest deletes that
      directory before the run (`--basetemp=.` would wipe the worktree).
    - `--junitxml`/`--junit-xml`/`--log-file` may write only to `/tmp` or
      `/dev/null`.
    - Long options must be known. Common flags such as `--lf`, `--ff`,
      `--sw`, `--co`, `--tb=`, `--maxfail=`, and `--no-header` are allowed.
      Others, such as `--cov-report` or `--debug`, are reported.
  - Git: read-only inspection only. Allowed: `status`, `diff`, `log`,
    `show`, `rev-parse`, `rev-list`, `ls-files`, `ls-tree`, `cat-file`,
    `blame`, `grep`, `describe`, `shortlog`, `merge-base`, `name-rev`,
    `version`, `show-ref`, `for-each-ref`, `check-ignore`, `diff-tree`,
    `diff-files`, `diff-index`, `range-diff`, `whatchanged`, and
    `count-objects`.
    - Every git write is reported, for example `add`, `commit`, `checkout`,
      `switch`, `stash`, `tag`, `branch`, `worktree`, `apply`,
      `cherry-pick`, `revert`, `mv`, `init`, `push`, `merge`, `rebase`,
      `reset`, and `clean`.
    - Also reported: global options other than `-C`/`--no-pager`/
      `--git-dir=`/`--work-tree=` (all workspace-only), `--output=`,
      `grep --open-files-in-pager`, and `--ext-diff`/`--textconv`.
- **Wrappers.** `env`, `nohup`, `timeout`, and `nice` are unwrapped, and
  the inner command is checked.
  - An unknown wrapper option is reported.
  - `env` with no command is reported, and so is an env assignment other
    than `CI`, `TERM`, `NO_COLOR`, `FORCE_COLOR`, `COLUMNS`, `LINES`,
    `LANG`, `LC_*`, or `TZ`.
- **Always reported.** These are examples, not a full list, since
  anything not named above is reported:
  - `xargs`, always: it adds arguments from stdin that cannot be checked.
  - `gh`, interpreters (`python`, `node`, `perl`, and the others,
    including any `-c`/`-e`/`-p` cluster such as `-Ic` or `-pe`), `awk`,
    `sed`, editors, `tar`, `cp`, `mv`, `rm`, `sudo`, `ssh`, `ping`,
    `openssl`, `printenv`, `docker`, and package installs.
  - `$(...)`, backticks, and `<(...)`.
  - Any shell expansion, inside URLs and quotes too: `${VAR}`, `$NAME`,
    `$1`, `$?`, and `$'...'`. A `$` is allowed only before whitespace,
    `|`, `)`, or the end of the command, so `grep 'x$' f` is reported too.
  - `DROP`/`TRUNCATE TABLE`/`DELETE FROM`.
- **Arguments.** Every argument is checked.
  - Secret-looking words or paths are reported: `.ssh`, `id_rsa`, `.env`,
    `credentials`, `.netrc`, `.aws`, `.omnigent/`, `secret`/`token`/
    `password`, key files, and credential-shaped values.
  - Absolute paths must be under `OMNIGENT_APPROVAL_WATCHER_WORKSPACES` or
    `/tmp`. Relative paths must not escape with `..`, and paths containing
    `$VAR` are reported.
  - URLs and `user@host:` targets must be loopback.
  - Output redirection (`>`, `>>`, `&>`) may target only `/dev/null`,
    `/dev/std*`, `/tmp`, or another fd. `echo x > README.md` is reported.

When the rules are unsure, they report. Unknown programs, unknown
options on wrappers and project tools, and shell forms they cannot parse
are all reported.

## Environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `OMNIGENT_MANAGER_WEBHOOK_SECRET` | yes | Same signing key the server exports. Never put it in YAML. |
| `OMNIGENT_MANAGER_WEBHOOK_SECRET_PREVIOUS` | no | Accepted during key rotation. |
| `OMNIGENT_APPROVAL_WATCHER_SERVER_URL` | yes | Origin of the Omnigent server that receives the resolve POST, e.g. `http://127.0.0.1:7878`. Must be loopback. |
| `OMNIGENT_APPROVAL_WATCHER_ALLOW_REMOTE_SERVER` | no | `1` allows a non-loopback server origin. Off by default. |
| `OMNIGENT_APPROVAL_WATCHER_TOKEN` | one of | Sent as `Authorization: Bearer <token>` on the resolve POST. |
| `OMNIGENT_APPROVAL_WATCHER_COOKIE` | one of | Sent as `Cookie:` when no token is set. |
| `OMNIGENT_APPROVAL_WATCHER_WORKSPACES` | no | Absolute workspace roots, `:`-separated. |
| `OMNIGENT_APPROVAL_WATCHER_AUDIT` | no | JSONL path. Default `/tmp/omnigent-approval-watcher.jsonl`. |
| `OMNIGENT_APPROVAL_WATCHER_PORT` | no | Listen port on `127.0.0.1`. Default `8787`. |

If the resolve POST fails (401, transport error, or redirect), the card is
recorded as `reported` with the HTTP status in `reason`.

## Server config snippet

Use this for the throwaway server only. Do not apply it to the live `:6767`
server until Jens enables the feature.

```yaml
manager_webhook:
  enabled: true
  endpoint: http://127.0.0.1:8787/
  allow_insecure_localhost: true
  # Same origin the host already serves. The live config sets none today,
  # so the web link falls back to OMNIGENT_PUBLIC_BASE_URL, if set.
  public_base_url: http://127.0.0.1:7878
  events:
    - session.awaiting_decision
```

## Try it on a throwaway server

```bash
cd <repo or worktree>
export OMNIGENT_MANAGER_WEBHOOK_SECRET="$(openssl rand -hex 32)"
SANDBOX="$(mktemp -d)"
# Point a throwaway server at "$SANDBOX" (config + data dir, port 7878) and
# put the snippet above in its config. Export the same secret for it.

OMNIGENT_APPROVAL_WATCHER_SERVER_URL=http://127.0.0.1:7878 \
OMNIGENT_APPROVAL_WATCHER_TOKEN="<token for the throwaway server>" \
OMNIGENT_APPROVAL_WATCHER_WORKSPACES="$PWD" \
OMNIGENT_APPROVAL_WATCHER_AUDIT="$SANDBOX/approval-watcher.jsonl" \
  python -m scripts.approval_watcher
```

Then run `omnigent manager-webhook test` against the throwaway server. It
should get `204`, because the test event is not `session.awaiting_decision`.
Next, start a Codex session there that asks to run `ls /tmp`; that card
should be approved. Then ask it to run `git push`; that card should be
reported and stay open in the web UI. Read the results with
`tail "$SANDBOX/approval-watcher.jsonl"`.
