#!/usr/bin/env bash
# Open a PR that merges upstream main (omnigent-ai/omnigent) into the fork's
# main (jholze/omnigent). Never pushes to upstream and never merges: the PR
# goes through the normal Omnigent review + Fork CI + merge path.
#
# Usage: scripts/fork/upstream-sync.sh [--repo PATH] [--dry-run] [--summary FILE]
# Exit codes: 0 PR opened/updated or already current, 2 merge conflict,
#             3 an open sync PR already exists, 1 any other error.
# Every run (including failures) rewrites a short Markdown summary, by default
# ~/Library/Logs/omnigent-upstream-sync-last.md, so a conflict is visible
# without reading the launchd log. Error exits stay non-zero on purpose.
set -euo pipefail

REPO="${HOME}/dev/omnigent-src"
DRY_RUN=0
SUMMARY="${HOME}/Library/Logs/omnigent-upstream-sync-last.md"
FORK="jholze/omnigent"
UPSTREAM_URL="https://github.com/omnigent-ai/omnigent.git"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

STATUS="error"
DETAILS=""
# write_summary STATUS — rewrite the summary file from STATUS and DETAILS.
write_summary() {
  mkdir -p "$(dirname "${SUMMARY}")" 2>/dev/null || true
  {
    echo "# Omnigent upstream sync: ${1}"
    echo
    echo "- Run: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "- Repo: ${REPO}"
    echo "- origin/main: $(git -C "${REPO}" rev-parse --short origin/main 2>/dev/null || echo '?')"
    echo "- upstream/main: $(git -C "${REPO}" rev-parse --short upstream/main 2>/dev/null || echo '?')"
    echo "- Fork-only / upstream-only commits: $(git -C "${REPO}" rev-list --left-right --count origin/main...upstream/main 2>/dev/null | tr '\t' '/' || echo '?')"
    if [ -n "${DETAILS}" ]; then
      echo
      printf '%s\n' "${DETAILS}"
    fi
  } > "${SUMMARY}.tmp" 2>/dev/null && mv "${SUMMARY}.tmp" "${SUMMARY}" || true
}
on_exit_summary() {
  rc=$?
  if [ "${STATUS}" = "error" ]; then
    DETAILS="Exit code ${rc}. See ~/Library/Logs/omnigent-upstream-sync.log.${DETAILS:+
${DETAILS}}"
  fi
  write_summary "${STATUS}"
}
trap on_exit_summary EXIT
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

open_pr="$(gh pr list -R "${FORK}" --state open --limit 100 --json number,headRefName \
  --jq '[.[] | select(.headRefName | startswith("sync/upstream"))][0] | select(. != null) | "#\(.number) \(.headRefName)"')"
if [ -n "${open_pr}" ]; then
  echo "open sync PR exists: ${open_pr}; finish it first"
  STATUS="open sync PR exists (exit 3)"
  DETAILS="Open sync PR: ${open_pr}. Finish or close it; no new sync was attempted."
  exit 3
fi

git fetch -q origin main
git fetch -q upstream main
if git merge-base --is-ancestor upstream/main origin/main; then
  echo "fork main already contains upstream/main ($(git rev-parse --short upstream/main))"
  STATUS="already current (exit 0)"
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
trap 'cleanup; on_exit_summary' EXIT
git worktree add -q -B "${branch}" "${wt}" origin/main
cd "${wt}"
if ! merge_out="$(git merge --no-ff --no-edit -m "Merge upstream omnigent-ai/omnigent@${up_sha} into main" upstream/main 2>&1)"; then
  conflicts="$(git diff --name-only --diff-filter=U)"
  if [ -z "${conflicts}" ]; then
    # Not a content conflict (e.g. a fetch or identity error): report it as such.
    echo "merge of upstream/main (${up_sha}) failed without conflicts:"
    printf '%s\n' "${merge_out}" | tail -20
    git merge --abort >/dev/null 2>&1 || true
    STATUS="merge failed without conflicts (exit 1)"
    DETAILS="git merge output (last 20 lines):

\`\`\`
$(printf '%s\n' "${merge_out}" | tail -20)
\`\`\`"
    exit 1
  fi
  echo "CONFLICT merging upstream/main (${up_sha}, ${behind} commits) into origin/main:"
  printf '%s\n' "${conflicts}" | sed 's/^/  /'
  git merge --abort || true
  echo "resolve on a sync/upstream-* branch (keep fork alembic chain and feature flags), then open the PR"
  STATUS="CONFLICT, manual resolution needed (exit 2)"
  DETAILS="Merging upstream/main ${up_sha} (${behind} commits) into origin/main conflicts in $(printf '%s\n' "${conflicts}" | grep -c .) files:

$(printf '%s\n' "${conflicts}" | sed 's/^/- /')

Next: create a worktree on sync/upstream-$(date +%Y%m%d) from origin/main, git merge upstream/main,
resolve (keep the fork alembic chain, feature flags, fork CI), regenerate openapi.json and
pnpm-lock.yaml, run the tests, then open the PR (docs/fork-workflow.md)."
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
  STATUS="dry-run clean merge (exit 0)"
  DETAILS="Clean merge of ${behind} upstream commits; alembic heads: ${heads}. Not pushed."
  exit 0
fi
git push -q origin "HEAD:refs/heads/${branch}"
pushed=1
gh pr create -R "${FORK}" --base main --head "${branch}" \
  --title "Upstream-Sync: omnigent-ai/omnigent@${up_sha}" \
  --body "Merges ${behind} upstream commits (omnigent-ai/omnigent main @ ${up_sha}) into the fork. Opened by scripts/fork/upstream-sync.sh. Alembic heads: ${heads}. Merge only after Omnigent review and green Fork CI (see docs/fork-workflow.md)."
pr_url="$(gh pr list -R "${FORK}" --state open --head "${branch}" --json url --jq '.[0].url' 2>/dev/null || true)"
STATUS="PR opened (exit 0)"
DETAILS="Sync PR: ${pr_url:-${branch}}. ${behind} upstream commits, alembic heads: ${heads}."
