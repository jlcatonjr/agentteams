#!/usr/bin/env bash
# Full remediation pass for one Linux host, from your workstation. DRY RUN by default.
#
#   ./run-host.sh <host>                          # audit + every step as a dry run
#   ./run-host.sh <host> --apply                  # OS security updates, Node, containers, app pins
#   ./run-host.sh <host> --apply --restart-services --reboot
#   ./run-host.sh <host> --apply --node-major 24
#   ./run-host.sh <host> --apply --allow-fresh    # Node fix inside the release cooldown (scan-detected vulnerability)
#   ./run-host.sh all                             # dry run across hosts.conf
#
# Order: L1 audit → L2 OS security updates → L3 Node runtime → L5 containers →
#        L4 each apps.conf entry that has pins → (apply) reboot if needed → L1 re-audit.
# A host showing compromise indicators is NOT patched in place (exit 4); nothing is changed
# either when the audit did not complete (exit 2 config / 3 incomplete) or the host is unreachable (1).
# Afterwards the exit is the worst step: 0 all ran clean, 1 a step reported action needed,
# 3 a step failed (see its log), 4 the post-change audit found compromise indicators.
# Flags: --apply  --restart-services  --reboot  --all-updates  --node-major N  --allow-fresh
# --allow-fresh is Rule S-10's remediation carve-out: L3 may take a Node release younger than the 14-day
# cooldown, for every Node install L3 finds on the host. Use it only when the vulnerability scan reported
# a Node runtime on this host as vulnerable;
# `all --allow-fresh` is refused (exit 2): name each flagged host.
# apps.conf's flags column tunes L4 only: an entry carrying --apply, --restart-services or --reboot is
# skipped (exit 3), so a dry run stays a dry run.
set -uo pipefail
set -f   # never glob-expand values read from hosts.conf / apps.conf
KIT="$(cd "$(dirname "$0")" && pwd)"
CONF="${AGENTTEAMS_SERVER_KIT_CONFIG:-$KIT}"
usage(){ sed -n '2,21p' "$0"; }
[ $# -ge 1 ] || { usage; exit 2; }
HOST="$1"; shift
APPLY=false; RESTART=false; REBOOT=false; ALLUPD=false; NODEMAJ=""; FRESH=false
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --restart-services) RESTART=true;; --reboot) REBOOT=true;;
  --all-updates) ALLUPD=true;; --allow-fresh) FRESH=true;;
  --node-major)
    if [ $# -lt 2 ] || ! [[ $2 =~ ^[0-9]+$ ]]; then
      echo "--node-major needs a major version number (e.g. --node-major 24)" >&2; usage >&2; exit 2
    fi
    NODEMAJ="$2"; shift;;
  *) echo "unknown option $1" >&2; exit 2;; esac; shift; done

# Host names: the same grammar as remote.sh (and vulnscan's MCP server); `all` is reserved.
HOST_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
valid_host(){ [[ $1 =~ $HOST_RE ]] && [ "$1" != "all" ]; }
HOST_RULE="names match [A-Za-z0-9][A-Za-z0-9._-]{0,63} and are not 'all'"

if [ "$HOST" = "all" ]; then
  # the carve-out covers only what the scan flagged, host by host
  ! $FRESH || { echo "--allow-fresh needs a host name, not 'all': use it only for a host whose Node runtime the scan flagged" >&2; exit 2; }
  [ -f "$CONF/hosts.conf" ] || { echo "no $CONF/hosts.conf — copy hosts.conf.example and edit it" >&2; exit 2; }
  names=(); bad=""
  while IFS= read -r h; do
    if valid_host "$h"; then names+=("$h"); else bad="$bad '$h'"; fi
  done < <(tr -d '\r' < "$CONF/hosts.conf" | grep -vE '^[[:space:]]*(#|$)' \
             | awk -F'|' '{n=$1; gsub(/^[[:space:]]+|[[:space:]]+$/,"",n); print n}')
  [ -z "$bad" ] || { echo "invalid host name(s) in $CONF/hosts.conf:$bad ($HOST_RULE)" >&2; exit 2; }
  FL=(); $APPLY && FL+=(--apply); $RESTART && FL+=(--restart-services); $REBOOT && FL+=(--reboot)
  $ALLUPD && FL+=(--all-updates); [ -n "$NODEMAJ" ] && FL+=(--node-major "$NODEMAJ")
  worst=0
  for h in ${names[@]+"${names[@]}"}; do
    bash "$0" "$h" ${FL[@]+"${FL[@]}"}; r=$?   # through bash: packaged copies may lack the exec bit
    [ "$r" -gt "$worst" ] && worst=$r
  done
  exit "$worst"
fi
valid_host "$HOST" || { echo "invalid host name '$HOST' ($HOST_RULE)" >&2; exit 2; }

R=(bash "$KIT/remote.sh")   # through bash: packaged copies may lack the exec bit
A=(); $APPLY && A=(--apply)
RS=(); $RESTART && RS=(--restart-services)

"${R[@]}" "$HOST" L1-audit.sh; pre=$?
case "$pre" in
  0|1) ;;
  255) echo "cannot reach $HOST — stopping"; exit 1;;
  4) echo; echo "COMPROMISE indicators on $HOST — not patching in place. Isolate, rebuild, rotate credentials."; exit 4;;
  2) echo "L1 audit of $HOST could not start (exit 2, see above) — stopping" >&2; exit 2;;
  *) echo "L1 audit of $HOST did not complete (exit $pre) — stopping before any change" >&2; exit 3;;
esac

WORST=0
step(){   # record a step's exit: 1 = action needed, 4 = compromise, anything else non-zero = failed (3)
  local rc=$1 m
  case "$rc" in 0) m=0;; 1) m=1;; 4) m=4;; *) m=3; echo "STEP FAILED (exit $rc) — see the log above" >&2;; esac
  [ "$m" -gt "$WORST" ] && WORST=$m
  return 0
}

L2=(${A[@]+"${A[@]}"} ${RS[@]+"${RS[@]}"}); $ALLUPD && L2+=(--all)
"${R[@]}" "$HOST" L2-os-patch.sh ${L2[@]+"${L2[@]}"}; step $?
L3=(${A[@]+"${A[@]}"} ${RS[@]+"${RS[@]}"}); [ -n "$NODEMAJ" ] && L3+=(--major "$NODEMAJ"); $FRESH && L3+=(--allow-fresh)
"${R[@]}" "$HOST" L3-node-runtime.sh ${L3[@]+"${L3[@]}"}; step $?
"${R[@]}" "$HOST" L5-containers.sh ${A[@]+"${A[@]}"}; step $?

if [ -f "$CONF/apps.conf" ]; then
  # Read in this shell (not a pipeline subshell) so each L4 exit reaches WORST.
  while IFS='|' read -r h unit dir py flags health pins; do
    trim(){ printf '%s\n' "$1" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'; }
    [ "$(trim "$h")" = "$HOST" ] || continue
    unit=$(trim "$unit"); dir=$(trim "$dir"); py=$(trim "$py"); flags=$(trim "$flags"); health=$(trim "$health"); pins=$(trim "$pins")
    if [ -z "$pins" ]; then echo; echo "SKIP $unit: no pins in apps.conf (take them from the L1 vulnerability report)"; continue; fi
    args=(--unit "$unit" --dir "$dir" --python "$py")
    [ -n "$health" ] && [ "$health" != "-" ] && args+=(--health "$health")
    # The flags column tunes L4; whether to change anything is decided here, never there. Split it exactly
    # as it is passed (IFS: space, tab, newline), then judge every word.
    fl=(); read -r -a fl <<< "$flags"
    banned=""
    for f in ${fl[@]+"${fl[@]}"}; do
      case "$f" in --apply|--restart-services|--reboot) banned="$f";; esac
    done
    if [ -n "$banned" ]; then
      echo "SKIP $unit: apps.conf flags may not contain --apply, --restart-services or --reboot (found $banned)" >&2
      step 3; continue
    fi
    args+=(${fl[@]+"${fl[@]}"})
    for p in $pins; do args+=(--pin "$p"); done
    "${R[@]}" "$HOST" L4-python-app.sh "${args[@]}" ${A[@]+"${A[@]}"} < /dev/null; step $?
  done < <(tr -d '\r' < "$CONF/apps.conf" | grep -vE '^[[:space:]]*(#|$)')
fi

if $APPLY && $REBOOT; then "${R[@]}" "$HOST" L2-os-patch.sh --apply --reboot; step $?; fi
if $APPLY; then echo; echo "Post-change audit (after a scheduled reboot, re-run ./remote.sh $HOST L1-audit.sh in ~5 min):"; "${R[@]}" "$HOST" L1-audit.sh; step $?; fi
$APPLY || { echo; echo "DRY RUN complete for $HOST. Review the logs, then re-run with --apply."; }
[ "$WORST" -eq 0 ] || echo "run-host: worst step result for $HOST: exit $WORST" >&2
exit "$WORST"
