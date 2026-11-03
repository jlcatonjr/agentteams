#!/usr/bin/env bash
# Full remediation pass for one Linux host, from your workstation. DRY RUN by default.
#
#   ./run-host.sh <host>                          # audit + every step as a dry run
#   ./run-host.sh <host> --apply                  # OS security updates, Node, containers, app pins
#   ./run-host.sh <host> --apply --restart-services --reboot
#   ./run-host.sh <host> --apply --node-major 24
#   ./run-host.sh <host> --apply --allow-fresh    # Node fix inside the release cooldown (scan-detected vulnerability)
#   ./run-host.sh all                             # dry run across hosts.conf
#   ./run-host.sh <host> --level l2-security      # ONE level's plan (PLAN lines) and its PLANDIGEST, as a dry run
#   ./run-host.sh <host> --level l4-pins --apply --expect-digest <sha256>   # apply exactly that plan (server-apply)
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
# Levels (vfr-s server-apply; the executor runs exactly one per op): l2-security (distro security updates only, restarts
# only --restart-unit units), l4-pins (apps.conf pins), py-venv (apps.conf entries with --venv-base: a new venv beside the
# old, atomic switch, old kept), remove-path (--remove-path P: quarantine, never delete). With --level: one host, no
# --reboot/--all-updates/--node-major/--allow-fresh/--restart-services, never L3 or L5; --apply needs --expect-digest,
# the sha256 of the normalised plan, recomputed on the host just before any change. Exits: 5 the plan changed (nothing
# changed), 6 a health check failed and the pre-state was restored, 7 a health check failed and it was not restored.
set -uo pipefail
set -f   # never glob-expand values read from hosts.conf / apps.conf
KIT="$(cd "$(dirname "$0")" && pwd)"
CONF="${AGENTTEAMS_SERVER_KIT_CONFIG:-$KIT}"
usage(){ sed -n '2,29p' "$0"; }
[ $# -ge 1 ] || { usage; exit 2; }
HOST="$1"; shift
APPLY=false; RESTART=false; REBOOT=false; ALLUPD=false; NODEMAJ=""; FRESH=false
LEVEL=""; NLEVEL=0; EXPECT=""; RUNITS=(); MAJORS=(); RPATH=""; NRPATH=0
lval(){ case "${2-}" in ""|--*) echo "$1 needs a value" >&2; exit 2;; esac; }
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --restart-services) RESTART=true;; --reboot) REBOOT=true;;
  --all-updates) ALLUPD=true;; --allow-fresh) FRESH=true;;
  --level) lval "$@"; LEVEL="$2"; NLEVEL=$((NLEVEL+1)); shift;;
  --expect-digest) lval "$@"; EXPECT="$2"; shift;;
  --restart-unit) lval "$@"; RUNITS+=("$2"); shift;;
  --major-allowed) lval "$@"; MAJORS+=("$2"); shift;;
  --remove-path) lval "$@"; RPATH="$2"; NRPATH=$((NRPATH+1)); shift;;
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

# server-apply levels: exactly one, one host, and none of the flags that widen a change.
lrefuse(){ echo "refused: $*" >&2; exit 2; }
UNIT_RE='^[A-Za-z0-9][A-Za-z0-9@._:-]{0,120}[.]service$'
if [ "$NLEVEL" -gt 0 ]; then
  [ "$NLEVEL" -eq 1 ] || lrefuse "exactly one --level per run"
  case "$LEVEL" in l2-security|l4-pins|py-venv|remove-path) ;; *) lrefuse "unknown level '$LEVEL'";; esac
  [ "$HOST" != "all" ] || lrefuse "a level runs on one named host, never 'all'"
  if $REBOOT || $ALLUPD || $FRESH || $RESTART || [ -n "$NODEMAJ" ]; then
    lrefuse "--level never takes --reboot, --all-updates, --node-major, --allow-fresh or --restart-services"
  fi
  if $APPLY; then [[ $EXPECT =~ ^[0-9a-f]{64}$ ]] || lrefuse "--level --apply needs --expect-digest <sha256 of the plan>"
  else [ -z "$EXPECT" ] || lrefuse "--expect-digest is for --apply"; fi
  for u in ${RUNITS[@]+"${RUNITS[@]}"} ${MAJORS[@]+"${MAJORS[@]}"}; do [[ $u =~ $UNIT_RE ]] || lrefuse "bad unit '$u'"; done
  [ ${#RUNITS[@]} -eq 0 ] || [ "$LEVEL" = l2-security ] || lrefuse "--restart-unit is for l2-security"
  [ ${#MAJORS[@]} -eq 0 ] || [ "$LEVEL" = l4-pins ] || [ "$LEVEL" = py-venv ] || lrefuse "--major-allowed is for l4-pins/py-venv"
  if [ "$LEVEL" = remove-path ]; then [ "$NRPATH" -eq 1 ] || lrefuse "remove-path needs exactly one --remove-path"
  else [ "$NRPATH" -eq 0 ] || lrefuse "--remove-path is for the remove-path level"; fi
elif [ -n "$EXPECT$RPATH" ] || [ ${#RUNITS[@]} -gt 0 ] || [ ${#MAJORS[@]} -gt 0 ]; then
  lrefuse "--expect-digest, --restart-unit, --major-allowed and --remove-path need --level"
fi

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

if [ -n "$LEVEL" ]; then
  TAB=$(printf '\t')
  # The normalised plan's sha256: PLAN lines only, CR dropped, blank runs squeezed, sorted (C locale), unique. The same
  # function is in every level script (and server_apply.plan_digest): a test keeps the copies identical.
  plan_digest(){
    tr -d '\r' | LC_ALL=C grep "^PLAN$TAB" | LC_ALL=C sed -E 's/[[:space:]]+/ /g; s/ $//' | LC_ALL=C sort -u \
      | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | awk '{print $1}'
  }
  has(){ local x="$1" y; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }
  TMPD=$(mktemp -d "${TMPDIR:-/tmp}/agentteams-level.XXXXXX") || exit 3
  trap 'rm -rf "$TMPD"' EXIT
  NSTEP=0
  # Steps: one remote call each, numbered in a fixed order so the plan pass and the apply pass match.
  add_step(){ printf '%s\0' "$@" > "$TMPD/step.$NSTEP"; NSTEP=$((NSTEP+1)); }
  case "$LEVEL" in
    l2-security)
      a=(L2-os-patch.sh --level); for u in ${RUNITS[@]+"${RUNITS[@]}"}; do a+=(--restart-unit "$u"); done; add_step "${a[@]}";;
    remove-path) add_step L7-remove-path.sh --level --path "$RPATH";;
    l4-pins|py-venv)
      if [ -f "$CONF/apps.conf" ]; then
        while IFS='|' read -r h unit dir py flags health pins; do
          trim(){ printf '%s\n' "$1" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'; }
          [ "$(trim "$h")" = "$HOST" ] || continue
          unit=$(trim "$unit"); dir=$(trim "$dir"); py=$(trim "$py"); flags=$(trim "$flags"); health=$(trim "$health"); pins=$(trim "$pins")
          [ -n "$pins" ] || continue
          fl=(); read -r -a fl <<< "$flags"; venv=false; bad=""
          for f in ${fl[@]+"${fl[@]}"}; do case "$f" in
            --venv-base) venv=true;;
            --apply|--restart-services|--reboot|--level|--expect-digest|--major-allowed|--plan|--git-pull|--allow-sdist|--requirements) bad="$f";;
          esac; done
          if [ -n "$bad" ]; then echo "SKIP $unit: apps.conf flags may not contain $bad under --level" >&2; echo 3 > "$TMPD/bad"; continue; fi
          [[ $unit =~ $UNIT_RE ]] || { echo "SKIP $unit: not a <name>.service unit" >&2; echo 3 > "$TMPD/bad"; continue; }
          if [ "$LEVEL" = py-venv ]; then $venv || continue; s=L6-py-venv.sh; else ! $venv || continue; s=L4-python-app.sh; fi
          a=("$s" --level --unit "$unit" --dir "$dir" --python "$py")
          [ -n "$health" ] && [ "$health" != "-" ] && a+=(--health "$health")
          a+=(${fl[@]+"${fl[@]}"})
          for p in $pins; do a+=(--pin "$p"); done
          has "$unit" ${MAJORS[@]+"${MAJORS[@]}"} && a+=(--major-allowed)   # signed per app, never from apps.conf
          add_step "${a[@]}"
        done < <(tr -d '\r' < "$CONF/apps.conf" | grep -vE '^[[:space:]]*(#|$)')
      fi;;
  esac
  [ ! -f "$TMPD/bad" ] || { echo "stopping before any change: an apps.conf entry was refused" >&2; exit 3; }
  run_step(){   # $1 index, $2 out file, rest: extra args. Prints the output; returns the remote exit.
    local i="$1" out="$2" s; shift 2
    local a=(); while IFS= read -r -d '' s; do a+=("$s"); done < "$TMPD/step.$i"
    "${R[@]}" "$HOST" "${a[@]}" "$@" < /dev/null > "$out" 2>&1; local rc=$?
    cat "$out"; return "$rc"
  }
  echo; echo "== Level $LEVEL on $HOST: plan ($NSTEP step(s))"
  i=0; worst=0
  while [ "$i" -lt "$NSTEP" ]; do
    run_step "$i" "$TMPD/plan.$i"; rc=$?
    case "$rc" in 0|1) ;; 4) echo "COMPROMISE indicators during the plan — not patching in place."; exit 4;;
      2) echo "plan step $i refused (exit 2) — stopping before any change" >&2; exit 2;;
      *) echo "plan step $i failed (exit $rc) — stopping before any change" >&2; exit 3;; esac
    [ "$rc" -gt "$worst" ] && worst=$rc
    i=$((i+1))
  done
  digest=$(i=0; while [ "$i" -lt "$NSTEP" ]; do cat "$TMPD/plan.$i"; i=$((i+1)); done | plan_digest)   # set -f: no glob
  printf 'PLANDIGEST\t%s\t%s\n' "$LEVEL" "$digest"
  if ! $APPLY; then echo; echo "DRY RUN complete for $HOST (level $LEVEL). Apply exactly this plan: --apply --expect-digest $digest"; exit "$worst"; fi
  if [ "$digest" != "$EXPECT" ]; then
    echo "DIGEST MISMATCH: the plan on $HOST is $digest, not the confirmed $EXPECT — nothing changed" >&2; exit 5
  fi
  echo; echo "== Level $LEVEL on $HOST: apply"
  i=0; worst=0; comp=false
  while [ "$i" -lt "$NSTEP" ]; do
    sd=$(plan_digest < "$TMPD/plan.$i")
    run_step "$i" "$TMPD/apply.$i" --apply --expect-digest "$sd"; rc=$?
    case "$rc" in 0|1) ;; 4) comp=true;; 5|6|7) ;; *) rc=3;; esac
    [ "$rc" -gt "$worst" ] && worst=$rc
    if [ "$rc" -gt 1 ]; then echo "step $i ended with exit $rc — no further step on $HOST" >&2; break; fi
    i=$((i+1))
  done
  echo; echo "Post-change audit:"; "${R[@]}" "$HOST" L1-audit.sh; rc=$?
  [ "$rc" -eq 4 ] && comp=true
  case "$rc" in 0|1|4) ;; *) [ "$worst" -lt 3 ] && worst=3;; esac
  [ "$rc" -eq 1 ] && [ "$worst" -eq 0 ] && worst=1
  if $comp; then echo "COMPROMISE indicators after the change on $HOST" >&2; exit 4; fi
  [ "$worst" -eq 0 ] || echo "run-host: worst step result for $HOST (level $LEVEL): exit $worst" >&2
  exit "$worst"
fi

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
