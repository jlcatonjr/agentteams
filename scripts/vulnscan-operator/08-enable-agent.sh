#!/usr/bin/env bash
# 08 — switch on unattended agent mode (walkthrough Step 10). Run in your own interactive terminal.
#
#   1. sign the unattended grant: stage-fix + open-pr, only REPO_ROOT, DAYS days, USES uses
#      (you review every field and confirm with y; your key is named on this one command only).
#      At most two grants may be valid: with two already valid it first offers to revoke the one that
#      expires first, and exits without signing if you decline.
#   2. set agent.enabled true in vulnscan.json (the daily job then runs the agent after each scan)
#   3. install the daily job at HOUR:MINUTE local time (dry run first, then it asks you)
# Afterwards: read `review` every few days and `review --ack`; re-sign before the grant expires.
# To switch off: set agent.enabled false (or `07-schedule.sh --remove` to stop the job entirely).
#
# Usage:   KEY_ID=op1 REPO_ROOT=~/vulnscan-sandbox-root DAYS=7 USES=30 HOUR=9 MINUTE=0 \
#            bash scripts/vulnscan-operator/08-enable-agent.sh
set -euo pipefail

KEY_ID="${KEY_ID:-op1}"
REPO_ROOT="${REPO_ROOT:-$HOME/vulnscan-sandbox-root}"
DAYS="${DAYS:-7}"; USES="${USES:-30}"; HOUR="${HOUR:-9}"; MINUTE="${MINUTE:-0}"
GOV="$HOME/ops-team/.goose/recipes"
PRIV="$HOME/.config/agentteams/keys/decision-signing-$KEY_ID.pem"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
[ -t 0 ] || { echo "run this in an interactive terminal (signing asks you to confirm)" >&2; exit 2; }
[ -f "$PRIV" ] || { echo "no operator key $PRIV (run 03-configure.sh)" >&2; exit 2; }
[ -d "$REPO_ROOT" ] || { echo "no such repo root: $REPO_ROOT" >&2; exit 2; }
"$PY" -P -c 'import sys; from agentteams.vulnscan import daily, agent_run
ok, why = agent_run.selftest_ok(daily.load_config()); print("self-test:", why); sys.exit(0 if ok else 1)' \
  || { echo "no passing self-test for this image (run scripts/vulnscan-agent-image.sh)" >&2; exit 2; }
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-enable.XXXXXX")"; trap 'rmdir "$WORKDIR" 2>/dev/null || true' EXIT
cd "$WORKDIR"   # sign from an empty directory (a pipx install under ~/.local is refused from $HOME)

echo "== 1/3 sign the unattended grant: stage-fix + open-pr on $REPO_ROOT, $DAYS days, $USES uses"
# At most two grants may be valid (with a third, vulnscan refuses them all). `grant list` prints one JSON
# object per valid grant; with two already valid, offer to revoke the one that expires first.
OLDEST="$("${VS[@]}" grant list | "$PY" -P -c '
import json, sys
from datetime import datetime
rows = [json.loads(line) for line in sys.stdin if line.strip()]
if len(rows) >= 2:
    g = min(rows, key=lambda r: datetime.fromisoformat(r["expires"]))
    print(g["grant_id"], g["expires"])')"
if [ -n "$OLDEST" ]; then
  OLD_ID="${OLDEST%% *}"; OLD_EXP="${OLDEST#* }"
  echo "two grants are already valid; a third would make vulnscan refuse them all"
  read -r -p "Revoke $OLD_ID (expires $OLD_EXP) before signing? [y/N] " ANS
  case "$ANS" in
    y|Y) "${VS[@]}" grant revoke --grant-id "$OLD_ID" ;;
    *) echo "not signing: revoke a grant first (grant revoke --grant-id <grant_id>)" >&2; exit 1 ;;
  esac
fi
AGENTTEAMS_DECISION_ED25519_KEYFILE="$PRIV" "${VS[@]}" grant sign --key-id "$KEY_ID" --days "$DAYS" --max-uses "$USES" \
  --op stage-fix --op open-pr --repo-root "$REPO_ROOT" --governance-dir "$GOV"
"${VS[@]}" grant list

echo "== 2/3 agent.enabled = true"
"$PY" -P - "$HOME/.config/agentteams/vulnscan.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
cfg = json.loads(p.read_text())
cfg.setdefault("agent", {})["enabled"] = True
p.write_text(json.dumps(cfg, indent=2) + "\n")
from agentteams.vulnscan import daily
daily.load_config()
print("agent mode enabled; config valid")
PY

echo "== 3/3 the daily job at $(printf '%02d:%02d' "$HOUR" "$MINUTE") local time"
HOUR="$HOUR" MINUTE="$MINUTE" bash "$HERE/07-schedule.sh"
echo
echo "Unattended mode is on. Each morning: scan, then the agent (budget \$2/run), then a desktop notification."
echo "Review:  ${VS[*]} review     Acknowledge:  ${VS[*]} review --ack"
