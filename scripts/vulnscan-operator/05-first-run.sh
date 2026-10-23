#!/usr/bin/env bash
# 05 — the supervised first run (walkthrough Steps 8 and 9). Run in your own interactive terminal.
#
#   1. sign a NARROW grant: stage-fix + open-pr, only ~/vulnscan-sandbox-root, 1 day, 5 uses
#      (you review every field and confirm with y; your key is named on this one command only)
#   2. daily scan (agent mode stays off in the config; the scan writes today's run directory)
#   3. one supervised agent-run on today's scan (GLM 5.3 via OpenRouter, $2 budget cap)
#   4. the after-action review, and the gateway's spend record
# Watch it: Ctrl-C stops the run and removes both containers (exit 143).
#
# Usage:   KEY_ID=op1 bash scripts/vulnscan-operator/05-first-run.sh
set -euo pipefail

KEY_ID="${KEY_ID:-op1}"
ROOT="$HOME/vulnscan-sandbox-root"
GOV="$HOME/ops-team/.goose/recipes"
PRIV="$HOME/.config/agentteams/keys/decision-signing-$KEY_ID.pem"
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
[ -t 0 ] || { echo "run this in an interactive terminal (signing asks you to confirm)" >&2; exit 2; }
[ -f "$PRIV" ] || { echo "no operator key $PRIV (run 03-configure.sh)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 2; }
"$PY" -P -c 'import sys; from agentteams.vulnscan import daily, agent_run
ok, why = agent_run.selftest_ok(daily.load_config()); print("self-test:", why); sys.exit(0 if ok else 1)' \
  || { echo "no passing self-test for this image (run scripts/vulnscan-agent-image.sh)" >&2; exit 2; }

# Sign from an empty directory: never from inside a repository or the team directory, and not from $HOME
# either, because a pipx install lives under ~/.local and the install-location check refuses an install
# inside the current directory.
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-sign.XXXXXX")"
trap 'rmdir "$WORKDIR" 2>/dev/null || true' EXIT
cd "$WORKDIR"

echo "== 1/4 sign the narrow grant (review every field, then answer y)"
if "${VS[@]}" grant list 2>/dev/null | grep -q '"stage-fix"'; then
  echo "an active grant already exists:"; "${VS[@]}" grant list
else
  AGENTTEAMS_DECISION_ED25519_KEYFILE="$PRIV" "${VS[@]}" grant sign --key-id "$KEY_ID" --days 1 --max-uses 5 \
    --op stage-fix --op open-pr --repo-root "$ROOT" --governance-dir "$GOV"
fi

echo "== 2/4 daily scan"
set +e
"${VS[@]}" daily; rc=$?
set -e
echo "daily exit $rc (0 clean, 1 findings, 3 partially unscanned, 4 compromise)"

echo "== 3/4 supervised agent-run (Ctrl-C to stop it)"
set +e
"${VS[@]}" agent-run; rc=$?
set -e
echo "agent-run exit $rc (0 completed, 3 not run or agent error, 143 stopped)"

echo "== 4/4 review and spend"
"${VS[@]}" review || true
RUN="$(ls -1d "$HOME/.local/state/agentteams/vulnscan"/20*-*-* 2>/dev/null | tail -1)"
[ -n "$RUN" ] && [ -f "$RUN/gateway/spend.json" ] && { echo "spend ($RUN/gateway/spend.json):"; cat "$RUN/gateway/spend.json"; echo; }
[ -n "$RUN" ] && [ -f "$RUN/agent/status.json" ] && { echo "status:"; cat "$RUN/agent/status.json"; echo; }
echo
echo "Check: the PR (if any) on your vulnscan-sandbox repo; spend.json calls > 0, prompt_tokens > 0, unmetered = 0."
echo "When satisfied:  ${VS[*]} review --ack"
echo "Report the result (exit codes, the review headline, spend.json); never include your keys."
