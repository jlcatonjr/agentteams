#!/usr/bin/env bash
# Programmatic auto-repair for the Goose OpenRouter route proxy.
# 1) plain goose-recover.py heals an absent proxy + repairable tracker, no-ops when healthy (exit 0)
# 2) exit 2 (refused; usually LISTENING-BUT-HUNG) -> escalate to --force-restart, which verifies
#    the PID's identity from the process table (not the network) and SIGTERM/SIGKILLs only our own
#    proxy; an unknown listener is still refused. A healthy proxy is never bounced.
set -euo pipefail
REPO="${AGENTTEAMS_REPO:-$HOME/githubrepositories/agentteams}"
PY="${GOOSE_RECOVER_PY:-python3}"
LOG="${GOOSE_WATCHDOG_LOG:-$HOME/.config/goose/proxy-watchdog.log}"
ts() { date "+%Y-%m-%dT%H:%M:%S"; }
log() { printf '%s %s\n' "$(ts)" "$*" >>"$LOG"; }
mkdir -p "$(dirname "$LOG")"; cd "$REPO"
set +e; out="$("$PY" scripts/goose-recover.py 2>&1)"; rc=$?; set -e
log "[check] recover exit=$rc :: ${out//$'\n'/ | }"
case "$rc" in
  0) exit 0 ;;
  2) log "[heal] escalating to --force-restart"
     set +e; out="$("$PY" scripts/goose-recover.py --force-restart 2>&1)"; frc=$?; set -e
     log "[heal] force-restart exit=$frc :: ${out//$'\n'/ | }"
     [ "$frc" -ne 0 ] && log "[FAILED] auto-repair failed (unknown listener or port still held). Operator attention needed."
     exit "$frc" ;;
  *) log "[FAILED] recover runtime error exit=$rc"; exit "$rc" ;;
esac
