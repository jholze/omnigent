#!/usr/bin/env bash
# Deploy a fork commit to the local launchd-managed Omnigent.
#
# Usage:
#   scripts/fork/deploy-local.sh [SHA]                    (default: origin/main)
#   scripts/fork/deploy-local.sh --rollback <old-sha>
#   scripts/fork/deploy-local.sh --rollback-mode <backup-dir>
#
# Local mode (no ~/Library/LaunchAgents/ai.omnigent.server.plist): the host
# job runs `service_entry --local` and spawns the server. Stop once with
# `omnigent server stop` and let launchd (KeepAlive) start the host again.
# Kickstart the host once, without -k, only if no server process is back
# within 180s. Kickstarting while a server is already coming up started a
# second server before.
#
# Server mode (ai.omnigent.server.plist exists): ai.omnigent.server runs
# `omnigent.cli server` (KeepAlive) and ai.omnigent.host runs
# `service_entry --server`. Restart with one `launchctl kickstart -k` of the
# server, wait up to 180s for exactly one server process that is not a
# pre-restart pid and /health 200, then one `launchctl kickstart -k` of the
# host. The host wait also requires the pre-kickstart host pids to be gone.
# Do not kickstart either job a second time.
#
# --rollback reinstalls ~/omnigent-backups/wheels-<sha> and restarts whichever
# mode is active. --rollback-mode switches back to local mode from a backup
# directory that holds the pre-switch host plist (--local) and any
# local_server.* files. Plists hold secrets: copy or move them, never print
# them.
set -euo pipefail
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

SRC="${HOME}/dev/omnigent-src"
BUILD="${HOME}/dev/omnigent-deploy"
BACKUPS="${HOME}/omnigent-backups"
DB="${HOME}/.omnigent/chat.db"
DATA_DIR="${HOME}/.omnigent"
LAUNCH_AGENTS="${HOME}/Library/LaunchAgents"
HOST_PLIST="${LAUNCH_AGENTS}/ai.omnigent.host.plist"
SERVER_PLIST="${LAUNCH_AGENTS}/ai.omnigent.server.plist"
URL="http://127.0.0.1:6767"
TOOL_PY="${HOME}/.local/share/uv/tools/omnigent/bin/python"
SERVER_PATTERN="omnigent.cli server --host 127.0.0.1 --port 6767"
HOST_SERVER_PATTERN="omnigent.host.service_entry --server"
HOST_LOCAL_PATTERN="omnigent.host.service_entry --local"
# Operational limits. The defaults are the deploy contract; a test may shorten
# them. Unset in normal use.
LOCAL_RESTART_POLLS="${DEPLOY_LOCAL_RESTART_POLLS:-60}"
LOCAL_KICKSTART_POLLS="${DEPLOY_LOCAL_KICKSTART_POLLS:-40}"
SERVER_HEALTH_WAIT="${DEPLOY_LOCAL_SERVER_HEALTH_WAIT:-180}"
HOST_PROCESS_WAIT="${DEPLOY_LOCAL_HOST_PROCESS_WAIT:-60}"
BOOTOUT_POLLS="${DEPLOY_LOCAL_BOOTOUT_POLLS:-30}"
POLL_SLEEP="${DEPLOY_LOCAL_POLL_SLEEP:-3}"
VERIFY_SETTLE="${DEPLOY_LOCAL_VERIFY_SETTLE:-5}"

log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
server_pids() { pgrep -f "${SERVER_PATTERN}" || true; }
host_server_pids() { pgrep -f "${HOST_SERVER_PATTERN}" || true; }
host_local_pids() { pgrep -f "${HOST_LOCAL_PATTERN}" || true; }
count_sessions() { sqlite3 -readonly "${DB}" "select count(*) from conversations;"; }

# True when any pid in the space-separated list $1 is still running.
pids_alive() {
  local p list
  list="${1:-}"
  [ -n "${list}" ] || return 1
  # shellcheck disable=SC2086
  for p in ${list}; do
    [ -n "${p}" ] || continue
    if kill -0 "${p}" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# old_pids is a space-separated list of server pids captured before the restart.
alive_old() { pids_alive "${old_pids:-}"; }

line_count() {
  local blob="$1"
  if [ -z "${blob}" ]; then
    printf '0\n'
    return 0
  fi
  printf '%s\n' "${blob}" | wc -l | tr -d ' '
}

wheel_count() {
  local dir="$1" f n
  n=0
  for f in "${dir}"/*.whl; do
    [ -f "${f}" ] || continue
    n=$((n + 1))
  done
  printf '%s\n' "${n}"
}

first_omnigent_wheel() {
  local dir="$1" f
  for f in "${dir}"/omnigent-*-py3-none-any.whl; do
    if [ -f "${f}" ]; then
      printf '%s\n' "${f}"
      return 0
    fi
  done
  return 1
}

in_server_mode() { [ -f "${SERVER_PLIST}" ]; }

usage() {
  cat >&2 <<'EOF'
usage: scripts/fork/deploy-local.sh [SHA]
       scripts/fork/deploy-local.sh --rollback <old-sha>
       scripts/fork/deploy-local.sh --rollback-mode <backup-dir>
EOF
}

# One graceful stop; launchd (KeepAlive) brings the host back. Unchanged
# from the local-mode deploy: no kickstart unless the server is gone.
restart_local_mode() {
  local ok
  log "stopping server (old pids: ${old_pids:-none}); waiting for launchd"
  "${HOME}/.local/bin/omnigent" server stop || true
  ok=0
  for _ in $(seq 1 "${LOCAL_RESTART_POLLS}"); do
    sleep "${POLL_SLEEP}"
    if [ -n "$(server_pids)" ] && ! alive_old && curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1; then
      ok=1
      break
    fi
  done
  if [ "${ok}" != 1 ] && [ -z "$(server_pids)" ]; then
    log "launchd did not restart the host within 180s; one kickstart (no -k)"
    if ! launchctl kickstart "gui/$(id -u)/ai.omnigent.host"; then
      echo "kickstart of ai.omnigent.host failed" >&2
    fi
    for _ in $(seq 1 "${LOCAL_KICKSTART_POLLS}"); do
      sleep "${POLL_SLEEP}"
      curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1 && { ok=1; break; }
    done
  fi
}

# kickstart -k the dedicated server once, then the host once. A slow health
# check is not a reason to kickstart the server again.
restart_server_mode() {
  local uid deadline n old_host_pids
  uid="$(id -u)"
  log "server mode: kickstart -k ai.omnigent.server"
  if ! launchctl kickstart -k "gui/${uid}/ai.omnigent.server"; then
    echo "kickstart of ai.omnigent.server failed" >&2
    return 1
  fi
  deadline=$((SECONDS + SERVER_HEALTH_WAIT))
  while true; do
    n="$(line_count "$(server_pids)")"
    # A still-running pre-restart server is not the new process.
    if [ "${n}" = 1 ] && ! alive_old && curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1; then
      break
    fi
    if [ "${SECONDS}" -ge "${deadline}" ]; then
      echo "server did not reach exactly one healthy process within 180s; not kickstarting again" >&2
      return 1
    fi
    sleep "${POLL_SLEEP}"
  done
  old_host_pids="$(host_server_pids | tr '\n' ' ')"
  log "server mode: kickstart -k ai.omnigent.host (old pids: ${old_host_pids:-none})"
  if ! launchctl kickstart -k "gui/${uid}/ai.omnigent.host"; then
    echo "kickstart of ai.omnigent.host failed" >&2
    return 1
  fi
  deadline=$((SECONDS + HOST_PROCESS_WAIT))
  while true; do
    n="$(line_count "$(host_server_pids)")"
    if [ "${n}" = 1 ] && ! pids_alive "${old_host_pids}"; then
      return 0
    fi
    if [ "${SECONDS}" -ge "${deadline}" ]; then
      echo "expected exactly 1 omnigent.host.service_entry --server process" >&2
      return 1
    fi
    sleep "${POLL_SLEEP}"
  done
}

verify_deploy() {
  local n head_rev db_rev after health fail host_n single_user info_json
  sleep "${VERIFY_SETTLE}"
  n="$(line_count "$(server_pids)")"
  head_rev="$("${TOOL_PY}" -c "
import os, omnigent.db
from alembic.config import Config
from alembic.script import ScriptDirectory
c = Config(); c.set_main_option('script_location', os.path.join(os.path.dirname(omnigent.db.__file__), 'migrations'))
print(' '.join(ScriptDirectory.from_config(c).get_heads()))")"
  db_rev="$(sqlite3 -readonly "${DB}" 'select group_concat(version_num, " ") from alembic_version;')"
  after="$(count_sessions)"
  health="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "${URL}/health" || true)"
  log "server processes: ${n} | health: ${health} | alembic db=${db_rev} code=${head_rev} | sessions ${before} -> ${after}"
  "${HOME}/.local/bin/omnigent" --version
  fail=0
  [ "${n}" = 1 ] || { echo "expected exactly 1 server process, found ${n}" >&2; fail=1; }
  [ "${health}" = 200 ] || { echo "/health is ${health}" >&2; fail=1; }
  [ "${db_rev}" = "${head_rev}" ] || { echo "migration not at head" >&2; fail=1; }
  [ "${after}" -ge "${before}" ] || { echo "session count dropped" >&2; fail=1; }
  "${HOME}/.local/bin/omnigent" --version | grep -q "${sha:0:8}" || { echo "installed build is not ${short}" >&2; fail=1; }
  if [ "${server_mode}" = 1 ]; then
    host_n="$(line_count "$(host_server_pids)")"
    single_user="error"
    # /v1/info is unauthenticated capability flags. Keep the body off the log;
    # only the single_user token is printed, and only in the summary line.
    info_json="$(curl -fsS -m 5 "${URL}/v1/info" 2>/dev/null || true)"
    if [ -n "${info_json}" ]; then
      single_user="$(printf '%s' "${info_json}" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin).get("single_user")
except Exception:
    print("error")
    raise SystemExit(0)
if value is True:
    print("true")
elif value is False:
    print("false")
else:
    print("missing")
' 2>/dev/null || printf 'error\n')"
    fi
    unset info_json
    log "host --server processes: ${host_n} | /v1/info single_user: ${single_user}"
    [ "${host_n}" = 1 ] || { echo "expected exactly 1 service_entry --server, found ${host_n}" >&2; fail=1; }
    [ "${single_user}" = "false" ] || { echo "/v1/info single_user is ${single_user}, expected false" >&2; fail=1; }
  fi
  if [ "${fail}" = 1 ]; then
    echo "DEPLOY CHECK FAILED. Backups: ${BACKUPS}/db-${ts}, ${BACKUPS}/plist-${ts}" >&2
    exit 1
  fi
  log "deployed ${short}. Rollback wheels stay in ${BACKUPS}/wheels-*."
}

# Switch launchd back to the host-spawned local server. Refuses before any
# bootout, plist move, or file restore when the backup has no host plist.
restore_local_mode() {
  local dir="$1" host_backup aside f base restored uid gone n health host_n local_n ok
  host_backup="${dir}/ai.omnigent.host.plist"
  if [ ! -d "${dir}" ]; then
    echo "--rollback-mode: not a directory: ${dir}" >&2
    exit 1
  fi
  if [ ! -f "${host_backup}" ] || [ ! -s "${host_backup}" ]; then
    echo "--rollback-mode: backup directory has no ai.omnigent.host.plist; refusing" >&2
    exit 1
  fi
  # Quiet marker check only. A server-mode deploy backup holds the --server
  # host plist; restoring it would leave no server job running. Do not print
  # the file.
  if ! { grep -q -- 'service_entry' "${host_backup}" && grep -q -- '--local' "${host_backup}"; }; then
    echo "--rollback-mode: backup host plist is not the --local service_entry job; refusing" >&2
    echo "Deploy backups taken in server mode hold the --server host plist and cannot be used with --rollback-mode." >&2
    exit 1
  fi
  if [ ! -d "${LAUNCH_AGENTS}" ]; then
    echo "--rollback-mode: ${LAUNCH_AGENTS} is not a directory" >&2
    exit 1
  fi
  if [ ! -d "${DATA_DIR}" ]; then
    echo "--rollback-mode: ${DATA_DIR} is not a directory" >&2
    exit 1
  fi

  aside="${BACKUPS}/plist-${ts}"
  mkdir -p "${aside}"
  chmod 700 "${aside}"
  if [ -f "${HOST_PLIST}" ]; then
    cp -p "${HOST_PLIST}" "${aside}/"
  fi
  if [ -f "${SERVER_PLIST}" ]; then
    cp -p "${SERVER_PLIST}" "${aside}/"
  fi
  log "saved current plists under ${aside} (contents not shown)"

  uid="$(id -u)"
  launchctl bootout "gui/${uid}/ai.omnigent.host" || true
  launchctl bootout "gui/${uid}/ai.omnigent.server" || true
  gone=0
  for _ in $(seq 1 "${BOOTOUT_POLLS}"); do
    if [ -z "$(server_pids)" ] && [ -z "$(host_server_pids)" ] && [ -z "$(host_local_pids)" ]; then
      gone=1
      break
    fi
    sleep "${POLL_SLEEP}"
  done
  if [ "${gone}" != 1 ]; then
    echo "processes still running after bootout; host plist was not replaced" >&2
    exit 1
  fi

  cp -p "${host_backup}" "${HOST_PLIST}"
  restored=0
  for f in "${dir}"/local_server.*; do
    [ -e "${f}" ] || continue
    base="$(basename "${f}")"
    cp -p "${f}" "${DATA_DIR}/${base}"
    restored=$((restored + 1))
  done
  log "restored ${restored} local_server.* file(s) from backup (contents not shown)"
  if [ -f "${SERVER_PLIST}" ]; then
    mv "${SERVER_PLIST}" "${aside}/ai.omnigent.server.plist"
    log "moved ai.omnigent.server.plist out of LaunchAgents"
  fi
  if ! launchctl bootstrap "gui/${uid}" "${HOST_PLIST}"; then
    echo "launchctl bootstrap of ai.omnigent.host failed. Current plists were saved in ${aside}" >&2
    exit 1
  fi

  log "waiting for the host to spawn the local server"
  ok=0
  for _ in $(seq 1 "${LOCAL_RESTART_POLLS}"); do
    sleep "${POLL_SLEEP}"
    if [ -n "$(server_pids)" ] && curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1; then
      ok=1
      break
    fi
  done
  if [ "${ok}" != 1 ] && [ -z "$(server_pids)" ]; then
    log "host did not spawn the server within 180s; one kickstart (no -k)"
    if ! launchctl kickstart "gui/${uid}/ai.omnigent.host"; then
      echo "kickstart of ai.omnigent.host failed" >&2
    fi
    for _ in $(seq 1 "${LOCAL_KICKSTART_POLLS}"); do
      sleep "${POLL_SLEEP}"
      curl -fsS -m 3 "${URL}/health" >/dev/null 2>&1 && { ok=1; break; }
    done
  fi
  n="$(line_count "$(server_pids)")"
  health="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "${URL}/health" || true)"
  host_n="$(line_count "$(host_server_pids)")"
  local_n="$(line_count "$(host_local_pids)")"
  log "server processes: ${n} | health: ${health} | host --local: ${local_n} | host --server: ${host_n}"
  if [ "${ok}" != 1 ] || [ "${n}" != 1 ] || [ "${health}" != 200 ] || [ "${local_n}" != 1 ] || [ "${host_n}" != 0 ] || [ -e "${SERVER_PLIST}" ]; then
    echo "ROLLBACK-MODE CHECK FAILED. Current plists were saved in ${aside}" >&2
    exit 1
  fi
  log "restored local mode. Previous plists stay in ${aside}."
}

main() {
  local rollback rollback_mode sha_arg rollback_dir server_mode sha short wheels ts whl
  rollback=0
  rollback_mode=0
  sha_arg=""
  rollback_dir=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --rollback)
        rollback=1
        shift
        [ -n "${1:-}" ] || { echo "--rollback needs the SHA of an existing wheels-<sha> backup" >&2; exit 1; }
        sha_arg="$1"
        shift
        ;;
      --rollback-mode)
        rollback_mode=1
        shift
        [ -n "${1:-}" ] || { echo "--rollback-mode needs a backup directory" >&2; exit 1; }
        rollback_dir="$1"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      -*)
        echo "unknown argument: $1" >&2
        usage
        exit 1
        ;;
      *)
        if [ -n "${sha_arg}" ]; then
          echo "unexpected argument: $1" >&2
          usage
          exit 1
        fi
        sha_arg="$1"
        shift
        ;;
    esac
  done
  if [ $# -gt 0 ]; then
    echo "unexpected argument: $1" >&2
    usage
    exit 1
  fi
  if [ "${rollback}" = 1 ] && [ "${rollback_mode}" = 1 ]; then
    echo "--rollback and --rollback-mode are mutually exclusive" >&2
    exit 1
  fi
  if [ "${rollback_mode}" = 1 ] && [ -n "${sha_arg}" ]; then
    echo "--rollback-mode does not take a SHA" >&2
    exit 1
  fi

  ts="$(date +%Y%m%d-%H%M%S)"
  server_mode=0
  if in_server_mode; then
    server_mode=1
  fi

  if [ "${rollback_mode}" = 1 ]; then
    log "rollback-mode from ${rollback_dir} (plist contents not shown)"
    restore_local_mode "${rollback_dir}"
    return 0
  fi

  if [ "${server_mode}" = 1 ]; then
    log "mode: server (ai.omnigent.server.plist present)"
  else
    log "mode: local"
  fi

  git -C "${SRC}" fetch -q origin main
  sha="$(git -C "${SRC}" rev-parse "${sha_arg:-origin/main}")"
  short="${sha:0:7}"
  wheels="${BACKUPS}/wheels-${short}"

  # 1. Wheels (built from a detached worktree so the source checkout stays untouched).
  if [ "${rollback}" = 1 ]; then
    [ -d "${wheels}" ] || { echo "no ${wheels} to roll back to" >&2; exit 1; }
  elif [ ! -f "${wheels}/omnigent-0.18.0.dev0-py3-none-any.whl" ] || [ "$(wheel_count "${wheels}")" -lt 3 ]; then
    log "building wheels for ${short} in ${BUILD}"
    git -C "${BUILD}" fetch -q origin main "${sha}" 2>/dev/null || git -C "${BUILD}" fetch -q origin main
    git -C "${BUILD}" checkout -q --detach "${sha}"
    mkdir -p "${wheels}"
    # setup.py skips the web build when a bundle from an earlier build is
    # present; force it so the wheel carries this commit's UI.
    (cd "${BUILD}" && unset OMNIGENT_SKIP_WEB_UI && export OMNIGENT_BUILD_WEB_UI=1 && \
      uv build -q --wheel -o "${wheels}" . && \
      uv build -q --wheel -o "${wheels}" sdks/python-client && \
      uv build -q --wheel -o "${wheels}" sdks/ui)
  fi
  whl="$(first_omnigent_wheel "${wheels}")" || { echo "no omnigent wheel in ${wheels}" >&2; exit 1; }
  [ "$(wheel_count "${wheels}")" -ge 3 ] || { echo "expected 3 wheels in ${wheels}" >&2; exit 1; }

  # 2. Backups. Plists hold secrets: copy them, never print them.
  mkdir -p "${BACKUPS}/plist-${ts}" "${BACKUPS}/db-${ts}"
  chmod 700 "${BACKUPS}/plist-${ts}" "${BACKUPS}/db-${ts}"
  cp -p "${HOST_PLIST}" "${BACKUPS}/plist-${ts}/"
  if [ "${server_mode}" = 1 ]; then
    cp -p "${SERVER_PLIST}" "${BACKUPS}/plist-${ts}/"
  fi
  log "backed up plists to ${BACKUPS}/plist-${ts} (contents not shown)"
  log "backing up DB to ${BACKUPS}/db-${ts}"
  sqlite3 "${DB}" ".backup '${BACKUPS}/db-${ts}/chat.db'"
  before="$(count_sessions)"
  old_pids="$(server_pids | tr '\n' ' ')"

  # 3. Install (same shape as the existing uv receipt).
  log "installing ${whl##*/} from ${wheels}"
  uv tool install -q --reinstall --python 3.12 --find-links "${wheels}" "omnigent[dictation] @ ${whl}"
  "${HOME}/.local/bin/omnigent" --version

  # 4. Restart the active launch layout.
  if [ "${server_mode}" = 1 ]; then
    if ! restart_server_mode; then
      echo "DEPLOY CHECK FAILED. Backups: ${BACKUPS}/db-${ts}, ${BACKUPS}/plist-${ts}" >&2
      exit 1
    fi
  else
    restart_local_mode
  fi

  # 5. Verify.
  verify_deploy
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
