#!/usr/bin/env bash
# Open a PR that merges upstream main (omnigent-ai/omnigent) into the fork's
# main (jholze/omnigent). Never pushes to upstream and never merges: the PR
# goes through the normal Omnigent review + Fork CI + merge path.
#
# Usage: scripts/fork/upstream-sync.sh [--repo PATH] [--dry-run]
# Exit codes: 0 PR opened/updated or already current, 2 merge conflict,
#             3 an open sync PR already exists and is healthy,
#             4 an open sync PR is blocked (older than the stale limit,
#               conflicting, or a failing check), 1 any other error.
#
# Every run writes a JSON status file (default
# ~/.omnigent/upstream-sync-status.json, override UPSTREAM_SYNC_STATUS_FILE)
# that a crash-report routine can read. A blocked PR (exit 4) and a merge
# conflict (exit 2) also log a WARNING line and, when configured, POST a
# notice to a webhook back channel. The webhook is read from
# UPSTREAM_SYNC_ENV_FILE (default ~/.config/omnigent/upstream-sync.env, mode
# 600) with UPSTREAM_SYNC_WEBHOOK_URL and UPSTREAM_SYNC_WEBHOOK_KEY. Values are
# never printed. UPSTREAM_SYNC_STALE_DAYS sets the stale limit (default 7).
set -euo pipefail

REPO="${HOME}/dev/omnigent-src"
DRY_RUN=0
FORK="jholze/omnigent"
UPSTREAM_URL="https://github.com/omnigent-ai/omnigent.git"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS_FILE="${UPSTREAM_SYNC_STATUS_FILE:-${HOME}/.omnigent/upstream-sync-status.json}"
ENV_FILE="${UPSTREAM_SYNC_ENV_FILE:-${HOME}/.config/omnigent/upstream-sync.env}"
STALE_DAYS="${UPSTREAM_SYNC_STALE_DAYS:-7}"
case "${STALE_DAYS}" in
  ''|*[!0-9.]*) echo "UPSTREAM_SYNC_STALE_DAYS must be a number, got '${STALE_DAYS}'; using 7" >&2; STALE_DAYS=7 ;;
esac

# write_status STATE MESSAGE [PR_JSON]: overwrite the status file atomically.
write_status() {
  mkdir -p "$(dirname "${STATUS_FILE}")"
  python3 - "${STATUS_FILE}" "$1" "$2" "${3:-null}" <<'PY'
import datetime, json, os, sys
path, state, message, pr = sys.argv[1:5]
doc = {
    "state": state,
    "message": message,
    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "pr": json.loads(pr),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
PY
}

# notify MESSAGE: POST to the webhook back channel when one is configured.
# Prints only whether it was sent and the HTTP status, never the URL or key.
notify() {
  local url="" key=""
  if [ -r "${ENV_FILE}" ]; then
    url="$(sed -n 's/^UPSTREAM_SYNC_WEBHOOK_URL=//p' "${ENV_FILE}" | tail -n 1)"
    key="$(sed -n 's/^UPSTREAM_SYNC_WEBHOOK_KEY=//p' "${ENV_FILE}" | tail -n 1)"
  fi
  if [ -z "${url}" ]; then
    echo "notify: no webhook configured (${ENV_FILE}); status file only"
    return 0
  fi
  local payload code
  payload="$(python3 -c 'import json,sys; print(json.dumps({"source": "omnigent-upstream-sync", "repo": sys.argv[2], "text": sys.argv[1]}))' "$1" "${FORK}")"
  if [ -n "${key}" ]; then
    # Key goes through a curl config on stdin so it never shows up in argv / ps.
    code="$(printf 'header = "Authorization: Bearer %s"\n' "${key}" | curl -sS --max-time 15 -o /dev/null \
      -w '%{http_code}' -K - -H 'Content-Type: application/json' -d "${payload}" "${url}" 2>/dev/null || true)"
  else
    code="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' \
      -H 'Content-Type: application/json' -d "${payload}" "${url}" 2>/dev/null || true)"
  fi
  echo "notify: webhook HTTP ${code:-error}"
}

cd "${REPO}"

# Guard rails: origin must be the fork, upstream must be fetch-only.
case "$(git remote get-url --push origin)" in
  *"github.com/${FORK}"|*"github.com/${FORK}.git") ;;
  *) echo "origin is not ${FORK}; refusing" >&2; exit 1 ;;
esac
if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream "${UPSTREAM_URL}"
fi
git remote set-url --push upstream DISABLED_no_push_to_upstream

open_prs="$(gh pr list -R "${FORK}" --state open --limit 100 \
  --json number,headRefName,createdAt,mergeable,statusCheckRollup,url)"
# Prints "<state>\t<summary>\t<pr json>" for the oldest open sync PR, or nothing.
open_pr="$(printf '%s' "${open_prs}" | python3 "${SCRIPT_DIR}/sync_pr_health.py" --stale-days "${STALE_DAYS}")"
if [ -n "${open_pr}" ]; then
  pr_state="$(printf '%s' "${open_pr}" | cut -f1)"
  pr_summary="$(printf '%s' "${open_pr}" | cut -f2)"
  pr_json="$(printf '%s' "${open_pr}" | cut -f3-)"
  if [ "${pr_state}" = "blocked" ]; then
    msg="WARNING: upstream sync is blocked: ${pr_summary}. No new sync PR is opened until it is merged or closed."
    echo "${msg}"
    write_status blocked "${msg}" "${pr_json}"
    notify "${msg}"
    exit 4
  fi
  echo "open sync PR exists: ${pr_summary}; finish it first"
  write_status open_pr "open sync PR exists: ${pr_summary}" "${pr_json}"
  exit 3
fi

git fetch -q origin main
git fetch -q upstream main
if git merge-base --is-ancestor upstream/main origin/main; then
  echo "fork main already contains upstream/main ($(git rev-parse --short upstream/main))"
  write_status current "fork main already contains upstream/main"
  exit 0
fi
behind="$(git rev-list --count origin/main..upstream/main)"
branch="sync/upstream-$(date +%Y%m%d)"
up_sha="$(git rev-parse --short upstream/main)"
wt="$(mktemp -d /tmp/upstream-sync.XXXXXX)"
pushed=0
cleanup() {
  cd "${REPO}"
  git worktree remove --force "${wt}" >/dev/null 2>&1 || true
  git worktree prune
  # The local branch is only a scratch ref unless it reached origin.
  if [ "${pushed}" = 0 ]; then git branch -D "${branch}" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
git worktree add -q -B "${branch}" "${wt}" origin/main
cd "${wt}"
if ! git merge --no-ff --no-edit -m "Merge upstream omnigent-ai/omnigent@${up_sha} into main" upstream/main >/dev/null 2>&1; then
  echo "CONFLICT merging upstream/main (${up_sha}, ${behind} commits) into origin/main:"
  git diff --name-only --diff-filter=U | sed 's/^/  /'
  git merge --abort || true
  echo "resolve on a sync/upstream-* branch (keep fork alembic chain and feature flags), then open the PR"
  if [ "${DRY_RUN}" = 0 ]; then
    msg="WARNING: upstream sync hit merge conflicts (${up_sha}, ${behind} commits); a sync/upstream-* branch must be resolved by hand."
    echo "${msg}"
    write_status conflict "${msg}"
    notify "${msg}"
  fi
  exit 2
fi
heads="$(python3 - <<'PY'
import pathlib, re
revs, downs = set(), set()
for p in pathlib.Path("omnigent/db/migrations/versions").glob("*.py"):
    t = p.read_text()
    r = re.search(r'^revision[^=]*=\s*["\']([^"\']+)', t, re.M)
    d = re.search(r'^down_revision[^=]*=\s*(.+)$', t, re.M)
    if r: revs.add(r.group(1))
    if d: downs.update(re.findall(r'["\']([^"\']+)["\']', d.group(1)))
print(" ".join(sorted(revs - downs)))
PY
)"
echo "alembic heads after merge: ${heads}"
case "${heads}" in *" "*) echo "WARNING: multiple alembic heads, the PR needs a merge migration" ;; esac
if [ "${DRY_RUN}" = 1 ]; then
  echo "dry-run: clean merge of ${behind} upstream commits, not pushed"
  exit 0
fi
git push -q origin "HEAD:refs/heads/${branch}"
pushed=1
pr_url="$(gh pr create -R "${FORK}" --base main --head "${branch}" \
  --title "Upstream-Sync: omnigent-ai/omnigent@${up_sha}" \
  --body "Merges ${behind} upstream commits (omnigent-ai/omnigent main @ ${up_sha}) into the fork. Opened by scripts/fork/upstream-sync.sh. Alembic heads: ${heads}. Merge only after Omnigent review and green Fork CI (see docs/fork-workflow.md).")"
echo "${pr_url}"
write_status pr_opened "opened ${pr_url} (${behind} upstream commits)"
