#!/usr/bin/env bash
# 06 — acceptance check 9: a SIGTERM during an agent run removes both containers (walkthrough Part D #9).
#
# Starts `agent-run` on the latest scan, sends it SIGTERM after DELAY seconds (default 20; what launchd sends
# on logout or `launchctl bootout`), and checks:
#   * agent-run exits 143;
#   * no agentteams-vulnscan container is left (agent and gateway);
#   * <run>/agent/status.json says agent-error, "terminated by SIGTERM".
# The agent may act under your grant before the signal (a few cents of model use, at most a grant use).
#
# Usage:   DELAY=20 bash scripts/vulnscan-operator/06-sigterm-check.sh
set -uo pipefail

DELAY="${DELAY:-20}"
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 2; }
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-sigterm.XXXXXX")"; trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

echo "== starting agent-run; SIGTERM in ${DELAY}s"
"$PY" -P -m agentteams.vulnscan agent-run >"$WORKDIR/out.json" 2>"$WORKDIR/err.txt" &
pid=$!
sleep "$DELAY"
running="$(docker ps --filter name=agentteams-vulnscan --format '{{.Names}}' | tr '\n' ' ')"
echo "containers before the signal: ${running:-none}"
kill -TERM "$pid" 2>/dev/null
wait "$pid"; rc=$?
sleep 2
left="$(docker ps -a --filter name=agentteams-vulnscan --format '{{.Names}}' | tr '\n' ' ')"
# the state dir as vulnscan resolves it (AGENTTEAMS_VULNSCAN_STATE_DIR, else $XDG_STATE_HOME, else ~/.local/state)
STATE_DIR="$("$PY" -P -c 'from agentteams.vulnscan import paths; print(paths.state_dir())')"
RUN="$(ls -1d "$STATE_DIR"/20*-*-* 2>/dev/null | tail -1)"
state="$("$PY" -P -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("run_state"), "|", d.get("reason",""))' "$RUN/agent/status.json" 2>/dev/null)"

ok=true
[ "$rc" = 143 ] && echo "PASS  exit code 143" || { echo "FAIL  exit code $rc (expected 143)"; ok=false; }
[ -z "$left" ] && echo "PASS  no vulnscan container left" || { echo "FAIL  containers left: $left (remove: docker rm -f $left)"; ok=false; }
case "$state" in "agent-error | terminated by SIGTERM"*) echo "PASS  status: $state";;
  *) echo "FAIL  status: ${state:-unreadable} (expected agent-error, terminated by SIGTERM)"; ok=false;; esac
[ -s "$WORKDIR/err.txt" ] && { echo "stderr:"; sed -n '1,10p' "$WORKDIR/err.txt"; }
$ok && echo "Check 9 passed." || { echo "Check 9 FAILED — report this output."; exit 1; }
