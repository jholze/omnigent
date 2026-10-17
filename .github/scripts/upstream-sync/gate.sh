#!/usr/bin/env bash
# Classify the fork's sync PR against the same REQUIRED list merge-ready uses.
# Exit 0: green (a missing DCO check does not block; this fork may not
#         have the DCO app installed. A present, red DCO check still fails.)
# Exit 10: checks still pending.
# Exit 1: at least one required check finished red.
#
# Env: GH_TOKEN, REPO, and the commit as $1.

set -euo pipefail

SHA="${1:?commit sha}"
export SHA
export GITHUB_OUTPUT="${GITHUB_OUTPUT:-$(mktemp)}"

log="$(mktemp)"
script_dir="$(cd "$(dirname "$0")" && pwd)"

set +e
bash "${script_dir}/../merge-ready/evaluate-checks.sh" >"${log}" 2>&1
rc=$?
set -e
cat "${log}"

if [[ "${rc}" -eq 0 ]]; then
  exit 0
fi

if grep -E 'conclusion=(failure|cancelled|timed_out|action_required|startup_failure|skipped|stale)' "${log}" >/dev/null; then
  exit 1
fi

# Everything else is green and only the DCO app never reported a check.
if grep -q '^MISSING : DCO$' "${log}" \
  && ! grep -E '^(NOT GREEN:|MISSING :)' "${log}" | grep -qv '^MISSING : DCO$'; then
  echo "DCO-Check ist auf diesem Fork nicht vorhanden; nur dieses Fehlen wird ignoriert."
  exit 0
fi

exit 10
