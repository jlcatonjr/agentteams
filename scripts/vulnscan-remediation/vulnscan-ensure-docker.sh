#!/usr/bin/env bash
# Make the vulnscan containers usable, or say exactly why not (macOS, Docker Desktop).
# Installed as ~/.config/agentteams/vulnscan-remediation/vulnscan-ensure-docker.sh by
# vulnscan-remediate-auto.sh --install; called by vulnscan-daily-wrapped.sh, vulnscan-remediate-auto.sh and
# vulnscan-deploy.sh (--no-start). User-level only: never sudo, never builds an image, never changes a grant.
#
#   vulnscan-ensure-docker.sh [--no-start] [--no-selftest]
#
# Exit codes (one line on stdout, "READY ..." or "<code> <message>"):
#   0  READY                     daemon answers, images present, self-test records match the images
#   10 infra.docker_daemon       daemon not answering (after starting Docker Desktop, unless --no-start)
#   11 infra.tools_image         tools image (or the enabled agent image) not built for the installed version
#   12 infra.selftest            a self-test failed
#   13 infra.no_gui_session      no GUI login session, so Docker Desktop cannot be started

set -uo pipefail
PY="$HOME/.local/pipx/venvs/agentteams/bin/python"
VS=("$PY" -P -m agentteams.vulnscan)
STATE="${AGENTTEAMS_VULNSCAN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/vulnscan}"
START_TIMEOUT="${VULNSCAN_DOCKER_START_TIMEOUT:-240}"
start=1 selftest=1
for a in "$@"; do
  case "$a" in --no-start) start=0 ;; --no-selftest) selftest=0 ;; *) echo "usage: $0 [--no-start] [--no-selftest]"; exit 2 ;; esac
done

log() { echo "$(date -u +%FT%TZ) ensure-docker: $*" >&2; }
out() { echo "$*"; }
daemon_up() { /usr/bin/perl -e 'alarm 15; exec @ARGV' docker version --format '{{.Server.Version}}' >/dev/null 2>&1; }

command -v docker >/dev/null || { out "infra.docker_daemon docker CLI not on this job's PATH"; exit 10; }

if ! daemon_up; then
  if [[ "$start" == 0 ]]; then out "infra.docker_daemon daemon not answering; --no-start given"; exit 10; fi
  launchctl print "gui/$(id -u)" >/dev/null 2>&1 || { out "infra.no_gui_session no GUI login session; Docker Desktop cannot start"; exit 13; }
  log "daemon not answering; starting Docker Desktop (open -g -a Docker)"
  open -g -a Docker || { out "infra.docker_daemon could not launch Docker.app"; exit 10; }
  waited=0
  until daemon_up; do
    (( waited >= START_TIMEOUT )) && { out "infra.docker_daemon started Docker Desktop; no answer after ${START_TIMEOUT}s"; exit 10; }
    sleep 5; waited=$((waited + 5))
  done
  log "daemon up after ${waited}s"
fi

# Image and self-test state, from the installed package's own checks (same tags and records the daily run uses).
state="$("$PY" -P - <<'EOF'
import json, subprocess
from agentteams.vulnscan import daily, subproc_confine as sc, agent_run as ar
cfg = daily.load_config()
tools = sc.image_tag(sc.settings(cfg))
why = sc.unavailable_reason(tools)   # image + self-test record + container flags, exactly as the executor checks
def has_image(tag):
    return bool(subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag],
                               capture_output=True, text=True, timeout=60).stdout.strip())
res = {"tools_tag": tools, "tools": "ok" if not why else ("selftest" if has_image(tools) else "image"),
       "tools_why": why, "agent": "off", "agent_tag": ""}
a = ar.agent_cfg(cfg)
if a.get("enabled"):
    tag = ar.image_tag(a); res["agent_tag"] = tag
    res["agent"] = "image" if not has_image(tag) else ("ok" if ar.selftest_ok(cfg)[0] else "selftest")
print(json.dumps(res))
EOF
)" || { out "infra.tools_image could not read the vulnscan config or image state"; exit 11; }
field() { /usr/bin/python3 -I -c 'import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$state" "$1"; }
tools="$(field tools)" agent="$(field agent)"

[[ "$tools" == image ]] && { out "infra.tools_image $(field tools_why)"; exit 11; }
[[ "$agent" == image ]] && { out "infra.tools_image agent image $(field agent_tag) is not built for the installed version"; exit 11; }

# Self-tests only when the recorded result does not match the current image (bounded: once per image change),
# under the wrapper lock (unless the wrapper already holds it: it called us) and the daily lock, so a self-test
# never rewrites a record while a daily run reads it, and never makes a daily exit 3 on a held daily.lock.
run_locked() {
  local pre=()
  [[ -z "${VDR_WRAPPER_LOCKED:-}" ]] && pre=(/usr/bin/lockf -t 900 "$STATE/wrapper.lock")
  ${pre[@]+"${pre[@]}"} "$PY" -P -c 'import fcntl, subprocess, sys, time
f = open(sys.argv[1], "a+")
for _ in range(20):
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
    except BlockingIOError:
        time.sleep(30)
else:
    sys.exit("daily.lock held for 10 min; self-test not run")
sys.exit(subprocess.call(sys.argv[2:]))' "$STATE/daily.lock" "$@"
}
if [[ "$tools" == selftest || "$agent" == selftest ]]; then
  [[ "$selftest" == 0 ]] && { out "infra.selftest self-test record does not match the current image; --no-selftest given"; exit 12; }
  if [[ "$tools" == selftest ]]; then
    log "tools-selftest (record does not match $(field tools_tag))"
    run_locked "${VS[@]}" tools-selftest >&2 || { out "infra.selftest tools-selftest failed for $(field tools_tag)"; exit 12; }
  fi
  if [[ "$agent" == selftest ]]; then
    log "agent-selftest (record does not match $(field agent_tag))"
    run_locked "${VS[@]}" agent-selftest >&2 || { out "infra.selftest agent-selftest failed for $(field agent_tag)"; exit 12; }
  fi
fi

out "READY docker $(docker version --format '{{.Server.Version}}' 2>/dev/null); $(field tools_tag); agent: $(field agent)"
exit 0
