#!/usr/bin/env bash
# Full remediation pass for one Linux host, from your workstation. DRY RUN by default.
#
#   ./run-host.sh <host>                          # audit + every step as a dry run
#   ./run-host.sh <host> --apply                  # OS security updates, Node, containers, app pins
#   ./run-host.sh <host> --apply --restart-services --reboot
#   ./run-host.sh <host> --apply --node-major 24
#   ./run-host.sh all                             # dry run across hosts.conf
#
# Order: L1 audit → L2 OS security updates → L3 Node runtime → L5 containers →
#        L4 each apps.conf entry that has pins → (apply) reboot if needed → L1 re-audit.
# A host showing compromise indicators is NOT patched in place (exit 4).
# Flags: --apply  --restart-services  --reboot  --all-updates  --node-major N
set -uo pipefail
set -f   # never glob-expand values read from hosts.conf / apps.conf
KIT="$(cd "$(dirname "$0")" && pwd)"
CONF="${AGENTTEAMS_SERVER_KIT_CONFIG:-$KIT}"
[ $# -ge 1 ] || { sed -n '2,14p' "$0"; exit 2; }
HOST="$1"; shift
APPLY=false; RESTART=false; REBOOT=false; ALLUPD=false; NODEMAJ=""
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --restart-services) RESTART=true;; --reboot) REBOOT=true;;
  --all-updates) ALLUPD=true;; --node-major) NODEMAJ="$2"; shift;;
  *) echo "unknown option $1" >&2; exit 2;; esac; shift; done

if [ "$HOST" = "all" ]; then
  for h in $(grep -vE '^[[:space:]]*(#|$)' "$CONF/hosts.conf" | awk -F'|' '{gsub(/ /,"",$1); print $1}'); do
    "$0" "$h" $($APPLY && echo --apply) $($RESTART && echo --restart-services) $($REBOOT && echo --reboot) \
         $($ALLUPD && echo --all-updates) ${NODEMAJ:+--node-major "$NODEMAJ"}
  done
  exit 0
fi

R=(bash "$KIT/remote.sh")   # through bash: packaged copies may lack the exec bit
A=(); $APPLY && A=(--apply)
RS=(); $RESTART && RS=(--restart-services)

"${R[@]}" "$HOST" L1-audit.sh; pre=$?
[ "$pre" -eq 255 ] && { echo "cannot reach $HOST — stopping"; exit 1; }
if [ "$pre" -ge 4 ]; then
  echo; echo "COMPROMISE indicators on $HOST — not patching in place. Isolate, rebuild, rotate credentials."; exit 4
fi

L2=(${A[@]+"${A[@]}"} ${RS[@]+"${RS[@]}"}); $ALLUPD && L2+=(--all)
"${R[@]}" "$HOST" L2-os-patch.sh ${L2[@]+"${L2[@]}"}
L3=(${A[@]+"${A[@]}"} ${RS[@]+"${RS[@]}"}); [ -n "$NODEMAJ" ] && L3+=(--major "$NODEMAJ")
"${R[@]}" "$HOST" L3-node-runtime.sh ${L3[@]+"${L3[@]}"}
"${R[@]}" "$HOST" L5-containers.sh ${A[@]+"${A[@]}"}

if [ -f "$CONF/apps.conf" ]; then
  grep -vE '^[[:space:]]*(#|$)' "$CONF/apps.conf" | while IFS='|' read -r h unit dir py flags health pins; do
    trim(){ echo "$1" | sed -E 's/^ +| +$//g'; }
    [ "$(trim "$h")" = "$HOST" ] || continue
    unit=$(trim "$unit"); dir=$(trim "$dir"); py=$(trim "$py"); flags=$(trim "$flags"); health=$(trim "$health"); pins=$(trim "$pins")
    if [ -z "$pins" ]; then echo; echo "SKIP $unit: no pins in apps.conf (take them from the L1 vulnerability report)"; continue; fi
    args=(--unit "$unit" --dir "$dir" --python "$py")
    [ -n "$health" ] && [ "$health" != "-" ] && args+=(--health "$health")
    # shellcheck disable=SC2206
    args+=($flags)
    for p in $pins; do args+=(--pin "$p"); done
    "${R[@]}" "$HOST" L4-python-app.sh "${args[@]}" ${A[@]+"${A[@]}"} < /dev/null
  done
fi

if $APPLY && $REBOOT; then "${R[@]}" "$HOST" L2-os-patch.sh --apply --reboot; fi
if $APPLY; then echo; echo "Post-change audit (after a scheduled reboot, re-run ./remote.sh $HOST L1-audit.sh in ~5 min):"; "${R[@]}" "$HOST" L1-audit.sh; fi
$APPLY || { echo; echo "DRY RUN complete for $HOST. Review the logs, then re-run with --apply."; }
