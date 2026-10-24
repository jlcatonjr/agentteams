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
# Exit    : the script's (L1: 0 clean · 1 action · 3 audit incomplete · 4 compromise) · 2 usage/config · 255 ssh
#           (`all`: the worst over the hosts, an unreachable host counting as 3)
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

[ $# -ge 2 ] || { sed -n '2,16p' "$0"; exit 2; }
HOST="$1"; SCRIPT="$2"; shift 2
# Only the kit's own L-scripts are streamed: no paths, no `..`, no other file beside this script.
case "$SCRIPT" in
  L1-audit.sh|L2-os-patch.sh|L3-node-runtime.sh|L4-python-app.sh|L5-containers.sh) ;;
  *) echo "refusing script '$SCRIPT': only L1-audit.sh, L2-os-patch.sh, L3-node-runtime.sh, L4-python-app.sh and L5-containers.sh run" >&2; exit 2;;
esac
[ -f "$KIT/$SCRIPT" ] || { echo "no such script: $SCRIPT" >&2; exit 2; }
# Host names: the same grammar as vulnscan's MCP server; `all` is reserved for "every host".
HOST_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
valid_host(){ [[ $1 =~ $HOST_RE ]] && [ "$1" != "all" ]; }
HOST_RULE="names match [A-Za-z0-9][A-Za-z0-9._-]{0,63} and are not 'all'"
if [ "$HOST" != "all" ] && ! valid_host "$HOST"; then echo "invalid host name '$HOST' ($HOST_RULE)" >&2; exit 2; fi
[ -f "$CONF/hosts.conf" ] || { echo "no $CONF/hosts.conf — copy hosts.conf.example and edit it" >&2; exit 2; }

# hosts.conf: CR and surrounding blanks/tabs are not part of a field (Python's str.strip(), as executor reads it).
conf_lines(){ tr -d '\r' < "$CONF/hosts.conf" | grep -vE '^[[:space:]]*(#|$)'; }
hosts(){ conf_lines | awk -F'|' '{n=$1; gsub(/^[[:space:]]+|[[:space:]]+$/,"",n); print n}'; }
target_of(){ conf_lines | awk -F'|' -v h="$1" '{n=$1; gsub(/^[[:space:]]+|[[:space:]]+$/,"",n); if(n==h){t=$2; gsub(/^[[:space:]]+|[[:space:]]+$/,"",t); print t}}'; }

IOC_END="__AGENTTEAMS_IOC_DATA_END__"
# Refuse a planted terminator or a binary file here, in the main shell and before any host is
# contacted: inside payload() an `exit` would only end the pipeline's subshell, and ssh would still run.
if [ "$SCRIPT" = "L1-audit.sh" ]; then
  for d in "${IOC_DIRS[@]}"; do
    for f in "$d"/ioc-*.txt; do
      [ -f "$f" ] || continue
      if grep -q "$IOC_END" "$f"; then echo "refusing $f: contains the data terminator" >&2; exit 2; fi
      # A NUL byte would be dropped by the remote shell; indicator data is text, so refuse instead.
      if [ "$(tr -d '\000' < "$f" | wc -c | tr -d ' ')" != "$(wc -c < "$f" | tr -d ' ')" ]; then
        echo "refusing $f: binary content (NUL bytes) — indicator files are plain text" >&2; exit 2
      fi
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
        tr -d '\r' < "$f"                     # CRLF → LF: a trailing CR would make every rule miss
        [ -n "$(tr -d '\r' < "$f" | tail -c 1)" ] && echo   # a missing final newline must not glue onto the terminator
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
  # An audit that never printed its verdict (no sudo, sudo wants a password, the shell died) is
  # incomplete (exit 3), never clean or "action needed"; indicator lines already printed still win (4).
  if [ "$SCRIPT" = "L1-audit.sh" ] && [ "$rc" -ne 255 ] && ! grep -qE '^RESULT (clean|action-needed|COMPROMISE)' "$log"; then
    echo "AUDIT INCOMPLETE for $host: no RESULT line (remote exit $rc)" >&2
    if [ "$rc" -eq 4 ] || grep -q "^IOC$(printf '\t')" "$log"; then rc=4; else rc=3; fi
  fi
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
  # Names are read line by line (never word-split or glob-expanded) and all are checked before any
  # host is contacted.
  names=(); bad=""
  while IFS= read -r h; do
    if valid_host "$h"; then names+=("$h"); else bad="$bad '$h'"; fi
  done < <(hosts)
  [ -z "$bad" ] || { echo "invalid host name(s) in $CONF/hosts.conf:$bad ($HOST_RULE)" >&2; exit 2; }
  worst=0
  for h in ${names[@]+"${names[@]}"}; do
    run_one "$h" "$@"; r=$?
    [ "$r" -eq 255 ] && r=3            # unreachable = unscanned, never clean
    [ "$r" -gt "$worst" ] && worst=$r
  done
  exit "$worst"
fi
run_one "$HOST" "$@"
