#!/usr/bin/env bash
# Run one L*-script against one host (or all hosts) over SSH, from your workstation.
# Scripts are streamed (`bash -s`), never copied. Root is used directly when the login
# is root, otherwise `sudo -n` (passwordless sudo for that account is required).
#
#   ./remote.sh <host> L1-audit.sh               # read-only audit + vulnerability report
#   ./remote.sh all    L1-audit.sh               # every host in hosts.conf
#   ./remote.sh <host> L2-os-patch.sh --apply    # extra args go to the script
#
# Config  : hosts.conf next to this script, or $AGENTTEAMS_SERVER_KIT_CONFIG/hosts.conf
# Logs    : ${AGENTTEAMS_SERVER_KIT_LOGS:-${XDG_STATE_HOME:-~/.local/state}/agentteams/server-kit/logs}
# L1 runs : every ioc-*.txt beside this script, then every ioc-*.txt in the config dir, is embedded as
#           data (never executed); the log is judged by `python -m agentteams.vulnscan inventory`
#           (skip: AGENTTEAMS_VULNSCAN_NO_REPORT=1; interpreter: AGENTTEAMS_PYTHON, default python3).
set -uo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
CONF="${AGENTTEAMS_SERVER_KIT_CONFIG:-$KIT}"
LOGS="${AGENTTEAMS_SERVER_KIT_LOGS:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/server-kit/logs}"
mkdir -p "$LOGS"

# Indicator data dirs: the packaged rules first, then the operator's own from the config dir (when it
# is a different dir). Both are embedded, even when two files share a basename: the operator's file
# can only ADD indicators, never replace or shadow a packaged one.
IOC_DIRS=("$KIT")
if [ -d "$CONF" ] && [ "$(cd "$CONF" && pwd -P)" != "$(cd "$KIT" && pwd -P)" ]; then
  IOC_DIRS+=("$CONF")
fi

[ $# -ge 2 ] || { sed -n '2,14p' "$0"; exit 2; }
HOST="$1"; SCRIPT="$2"; shift 2
[ -f "$KIT/$SCRIPT" ] || { echo "no such script: $SCRIPT" >&2; exit 2; }
[ -f "$CONF/hosts.conf" ] || { echo "no $CONF/hosts.conf — copy hosts.conf.example and edit it" >&2; exit 2; }

hosts(){ grep -vE '^[[:space:]]*(#|$)' "$CONF/hosts.conf" | awk -F'|' '{gsub(/^ +| +$/,"",$1); print $1}'; }
target_of(){ grep -vE '^[[:space:]]*(#|$)' "$CONF/hosts.conf" | awk -F'|' -v h="$1" '{n=$1; gsub(/^ +| +$/,"",n); if(n==h){t=$2; gsub(/^ +| +$/,"",t); print t}}'; }

IOC_END="__AGENTTEAMS_IOC_DATA_END__"
# Refuse a planted terminator here, in the main shell and before any host is contacted: inside
# payload() an `exit` would only end the pipeline's subshell, and ssh would still run.
if [ "$SCRIPT" = "L1-audit.sh" ]; then
  for d in "${IOC_DIRS[@]}"; do
    for f in "$d"/ioc-*.txt; do
      [ -f "$f" ] || continue
      if grep -q "$IOC_END" "$f"; then echo "refusing $f: contains the data terminator" >&2; exit 2; fi
    done
  done
fi
payload(){  # the script; for the audit, indicator rules are embedded as a *quoted* heredoc (inert data)
  if [ "$SCRIPT" = "L1-audit.sh" ]; then
    local d f
    printf "AGENTTEAMS_IOC_DATA=\$(cat <<'%s'\n" "$IOC_END"
    for d in "${IOC_DIRS[@]}"; do
      for f in "$d"/ioc-*.txt; do
        [ -f "$f" ] || continue
        cat "$f"
        [ -n "$(tail -c 1 "$f")" ] && echo   # a missing final newline must not glue onto the terminator
      done
    done
    printf '%s\n)\n' "$IOC_END"
  fi
  cat "$KIT/$SCRIPT"
}

run_one(){
  local host="$1"; shift
  local target; target="$(target_of "$host")"
  [ -n "$target" ] || { echo "unknown host '$host' (see $CONF/hosts.conf)" >&2; return 2; }
  local ssh_args; read -r -a ssh_args <<< "$target"
  local q="" a; for a in "$@"; do q="$q $(printf '%q' "$a")"; done
  local remote="if [ \"\$(id -u)\" -eq 0 ]; then bash -s --$q; else sudo -n bash -s --$q; fi"
  local log="$LOGS/$host-$(date -u +%Y%m%dT%H%M%SZ)-${SCRIPT%.*}.log"
  printf '\n######## %s :: %s %s ########\n' "$host" "$SCRIPT" "$*"
  payload | ssh -o ConnectTimeout=20 -o ServerAliveInterval=30 -o BatchMode=yes "${ssh_args[@]}" "$remote" 2>&1 | tee "$log"
  local rc=${PIPESTATUS[1]}
  [ "$rc" -eq 255 ] && echo "SSH FAILED for $host — check hosts.conf, keys, network" >&2
  if [ "$SCRIPT" = "L1-audit.sh" ] && [ "$rc" -ne 255 ] && [ -z "${AGENTTEAMS_VULNSCAN_NO_REPORT:-}" ]; then
    printf '\n---- vulnerability report (%s) ----\n' "$host"
    "${AGENTTEAMS_PYTHON:-python3}" -m agentteams.vulnscan inventory "$log" --host "$host" --out "${log%.log}-report.md"
    local orc=$?
    [ "$orc" -gt "$rc" ] && rc=$orc
  fi
  echo "log: $log   [exit $rc]"
  return "$rc"
}

if [ "$HOST" = "all" ]; then
  worst=0
  for h in $(hosts); do run_one "$h" "$@"; r=$?; [ "$r" -ne 255 ] && [ "$r" -gt "$worst" ] && worst=$r; done
  exit "$worst"
fi
run_one "$HOST" "$@"
