#!/usr/bin/env bash
# Deploy a fork commit to the local launchd-managed Omnigent server.
#
# Usage: scripts/fork/deploy-local.sh [SHA]   (default: origin/main)
#
# Steps: build wheels at SHA into ~/omnigent-backups/wheels-<sha>, back up the
# launchd plist and the SQLite DB, reinstall the uv tool from those wheels,
# stop the server once and let launchd restart it (no kickstart: that started
# a second server before), then verify. Check first that no important session
# is mid-turn (the restart interrupts live turns).
# Rollback: scripts/fork/deploy-local.sh --rollback <old-sha>  (reuses
# ~/omnigent-backups/wheels-<old-sha>; the DB backup is restored by hand).
set -euo pipefail
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

SRC="${HOME}/dev/omnigent-src"
BUILD="${HOME}/dev/omnigent-deploy"
BACKUPS="${HOME}/omnigent-backups"
DB="${HOME}/.omnigent/chat.db"
PLIST="${HOME}/Library/LaunchAgents/ai.omnigent.host.plist"
URL="http://127.0.0.1:6767"
TOOL_PY="${HOME}/.local/share/uv/tools/omnigent/bin/python"

rollback=0
if [ "${1:-}" = "--rollback" ]; then rollback=1; shift; fi
git -C "${SRC}" fetch -q origin main
sha="$(git -C "${SRC}" rev-parse "${1:-origin/main}")"
short="${sha:0:7}"
wheels="${BACKUPS}/wheels-${short}"
ts="$(date +%Y%m%d-%H%M%S)"
log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
server_pids() { pgrep -f "omnigent.cli server --host 127.0.0.1 --port 6767" || true; }
alive_old() { for p in ${old_pids:-}; do kill -0 "${p}" 2>/dev/null && return 0; done; return 1; }
count_sessions() { sqlite3 -readonly "${DB}" "select count(*) from conversations;"; }

# 1. Wheels (built from a detached worktree so the source checkout stays untouched).
if [ "${rollback}" = 1 ]; then
  [ -d "${wheels}" ] || { echo "no ${wheels} to roll back to" >&2; exit 1; }
elif [ ! -f "${wheels}/omnigent-0.18.0.dev0-py3-none-any.whl" ] || [ "$(ls "${wheels}"/*.whl | wc -l)" -lt 3 ]; then
  log "building wheels for ${short} in ${BUILD}"
  git -C "${BUILD}" fetch -q origin main
  git -C "${BUILD}" checkout -q --detach "${sha}"
  mkdir -p "${wheels}"
  (cd "${BUILD}" && unset OMNIGENT_SKIP_WEB_UI && \
    uv build -q --wheel -o "${wheels}" . && \
    uv build -q --wheel -o "${wheels}" sdks/python-client && \
    uv build -q --wheel -o "${wheels}" sdks/ui)
fi
whl="$(ls "${wheels}"/omnigent-*-py3-none-any.whl | head -n 1)"
[ "$(ls "${wheels}"/*.whl | wc -l)" -ge 3 ] || { echo "expected 3 wheels in ${wheels}" >&2; exit 1; }

# 2. Backups. The plist holds secrets: copy it, never print it.
mkdir -p "${BACKUPS}/plist-${ts}" "${BACKUPS}/db-${ts}"
chmod 700 "${BACKUPS}/plist-${ts}"
cp -p "${PLIST}" "${BACKUPS}/plist-${ts}/"
log "backing up DB to ${BACKUPS}/db-${ts}"
sqlite3 "${DB}" ".backup '${BACKUPS}/db-${ts}/chat.db'"
before="$(count_sessions)"
old_pids="$(server_pids | tr '\n' ' ')"

# 3. Install (same shape as the existing uv receipt).
log "installing ${whl##*/} from ${wheels}"
uv tool install -q --reinstall --python 3.12 --find-links "${wheels}" "omnigent[dictation] @ ${whl}"
"${HOME}/.local/bin/omnigent" --version

# 4. One graceful restart; launchd (KeepAlive) brings the host back.
log "stopping server (old pids: ${old_pids:-none}); waiting for launchd"
"${HOME}/.local/bin/omnigent" server stop || true
ok=0
for _ in $(seq 1 60); do
  sleep 3
  if [ -n "$(server_pids)" ] && ! alive_old && curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1; then
    ok=1; break
  fi
done
if [ "${ok}" != 1 ] && [ -z "$(server_pids)" ]; then
  log "launchd did not restart the host within 180s; one kickstart (no -k)"
  launchctl kickstart "gui/$(id -u)/ai.omnigent.host"
  for _ in $(seq 1 40); do sleep 3; curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1 && { ok=1; break; }; done
fi

# 5. Verify.
sleep 5
n="$(server_pids | wc -l | tr -d ' ')"
head_rev="$("${TOOL_PY}" -c "
import os, omnigent.db
from alembic.config import Config
from alembic.script import ScriptDirectory
c = Config(); c.set_main_option('script_location', os.path.join(os.path.dirname(omnigent.db.__file__), 'migrations'))
print(' '.join(ScriptDirectory.from_config(c).get_heads()))")"
db_rev="$(sqlite3 -readonly "${DB}" 'select group_concat(version_num, " ") from alembic_version;')"
after="$(count_sessions)"
health="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "${URL}/health")"
log "server processes: ${n} | health: ${health} | alembic db=${db_rev} code=${head_rev} | sessions ${before} -> ${after}"
"${HOME}/.local/bin/omnigent" --version
fail=0
[ "${n}" = 1 ] || { echo "expected exactly 1 server process, found ${n}" >&2; fail=1; }
[ "${health}" = 200 ] || { echo "/health is ${health}" >&2; fail=1; }
[ "${db_rev}" = "${head_rev}" ] || { echo "migration not at head" >&2; fail=1; }
[ "${after}" -ge "${before}" ] || { echo "session count dropped" >&2; fail=1; }
"${HOME}/.local/bin/omnigent" --version | grep -q "${sha:0:8}" || { echo "installed build is not ${short}" >&2; fail=1; }
if [ "${fail}" = 1 ]; then
  echo "DEPLOY CHECK FAILED. Backups: ${BACKUPS}/db-${ts}, ${BACKUPS}/plist-${ts}" >&2
  exit 1
fi
log "deployed ${short}. Rollback wheels stay in ${BACKUPS}/wheels-*."
