#!/usr/bin/env bash
# The daily and catch-up launchd jobs run this instead of
# `python -m agentteams.vulnscan daily [--catch-up]`, so "Docker ready" is a prerequisite of the same process
# (no race with a separate pre-flight job when the Mac wakes and launchd fires coalesced jobs together).
#
#   vulnscan-daily-wrapped.sh                  # 09:00 job: ensure Docker, then daily
#   vulnscan-daily-wrapped.sh --catch-up       # interval/login job: catch-up when stale, or retry an infra-partial run
#   vulnscan-daily-wrapped.sh --ensure-only    # drill: ensure Docker and print the result
#
# Bounded retry: when today's run exited partial because the tools container was unavailable, and Docker is now
# ready, the scan is re-run — at most MAX_INFRA_RETRIES times per date (counter in <run>/infra-retry.count), with the
# previous summary kept as summary.<HHMMSS>.{json,md}. Installed next to vulnscan-ensure-docker.sh.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.local/pipx/venvs/agentteams/bin/python"
VS=("$PY" -P -m agentteams.vulnscan)
STATE="${AGENTTEAMS_VULNSCAN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/vulnscan}"
MAX_INFRA_RETRIES="${MAX_INFRA_RETRIES:-2}"
ENSURE="$HERE/vulnscan-ensure-docker.sh"

# One wrapper at a time (the 09:00 and catch-up jobs fire together on wake): the second waits here instead of
# hitting the daily's non-blocking daily.lock and exiting 3. ensure-docker's self-tests take the same lock.
if [[ -z "${VDR_WRAPPER_LOCKED:-}" ]]; then
  mkdir -p "$STATE"
  export VDR_WRAPPER_LOCKED=1
  exec /usr/bin/lockf -t 1800 "$STATE/wrapper.lock" /bin/bash "$0" "$@"
fi

log() { echo "$(date -u +%FT%TZ) daily-wrapped: $*"; }
notify() { osascript -e "display notification \"${1//\"/\'}\" with title \"vulnscan daily\"" >/dev/null 2>&1 || true; }
ensure() { local line rc; line="$(bash "$ENSURE" "$@")"; rc=$?; log "ensure: $line"; [[ $rc != 0 ]] && notify "Docker not ready: $line"; return $rc; }

# Was today's run partial only because the tools container was unavailable? (0 = yes, retry is allowed)
infra_partial_today() {
  local run="$STATE/$(date +%F)" n
  [[ -f "$run/summary.json" ]] || return 1
  n="$(cat "$run/infra-retry.count" 2>/dev/null || true)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  (( n < MAX_INFRA_RETRIES )) || { log "infra retries for $(basename "$run") used up ($n/$MAX_INFRA_RETRIES)"; return 1; }
  "$PY" -P -c 'import json, sys
s = json.load(open(sys.argv[1]))
sys.exit(0 if any("tools container unavailable" in e for e in s.get("feed_errors", [])) else 1)' "$run/summary.json"
}

rescan() {   # keep the partial summary, run the daily scan again, count the retry only if a scan actually ran
  local run="$STATE/$(date +%F)" stamp n before rc m
  if "$PY" -P -c 'import sys; from agentteams.vulnscan import daily; sys.exit(0 if daily.run_in_progress() else 1)' 2>/dev/null; then
    log "a daily run is in progress; not starting a rescan"; return 3
  fi
  stamp="$(date +%H%M%S)"
  for f in summary.json summary.md; do [[ -f "$run/$f" ]] && cp -p "$run/$f" "$run/${f%.*}.$stamp.${f##*.}"; done
  n="$(cat "$run/infra-retry.count" 2>/dev/null || true)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  before="$(stat -f %m "$run/summary.json" 2>/dev/null || echo 0)"
  log "re-running the daily scan for $(basename "$run") (infra retry $((n + 1))/$MAX_INFRA_RETRIES; previous summary kept as summary.$stamp.*)"
  "${VS[@]}" daily; rc=$?
  if [[ "$(stat -f %m "$run/summary.json" 2>/dev/null || echo 0)" != "$before" ]]; then
    echo $((n + 1)) > "$run/infra-retry.count"
    for m in remediation.done remediation.blocked; do   # the new findings get a fresh remediation run (markers kept)
      if [[ -f "$run/$m" ]]; then mv "$run/$m" "$run/$m.cleared-$stamp"; log "re-armed remediation ($m kept as $m.cleared-$stamp)"; fi
    done
  else log "no new summary was written (daily.lock held elsewhere?); this retry is not counted"; fi
  return $rc
}

case "${1:-}" in
  --ensure-only) bash "$ENSURE"; exit $? ;;
  --catch-up)
    due="$("$PY" -P -c 'from agentteams.vulnscan import daily; print(int(daily.catch_up_due(daily.load_config())[0]))' 2>/dev/null || echo 0)"
    if [[ "$due" == 1 ]]; then ensure; "${VS[@]}" daily --catch-up; exit $?; fi
    if infra_partial_today && ensure; then rescan; exit $?; fi
    "${VS[@]}" daily --catch-up; exit $? ;;   # not due: prints the usual stale/partial warning, exits 0
  "")
    ensure; ready=$?
    "${VS[@]}" daily; code=$?
    if [[ $code == 3 ]] && infra_partial_today; then
      if [[ $ready != 0 ]]; then ensure && { rescan; code=$?; }
      else log "partial although Docker was ready before the scan; re-checking"; ensure && { rescan; code=$?; }; fi
    fi
    [[ $code == 3 ]] && notify "Daily scan is partially unscanned — see today's summary.md"
    exit $code ;;
  *) echo "usage: $0 [--catch-up | --ensure-only]"; exit 2 ;;
esac
