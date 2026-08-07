#!/usr/bin/env bash
#
# run_daily_judgment_audit.sh — the W14 judgment-layer measurement, once a day, unattended.
#
# Driven by launchd:
#   references/launchd/us.visualknowledge.agentteams.redteam-judgment.plist
#
# Runs `scripts/redteam_judgment_run.py` against the 14-payload corpus and records what it
# cost. This is TREND DETECTION, not assurance: the best model measured defends 7 of 11
# attacks. A clean day means the model behaved as it did yesterday, not that the judgment
# layer is sound.
#
# ---------------------------------------------------------------------------------------
# WHY A WRAPPER, AND NOT JUST A CRON LINE
# ---------------------------------------------------------------------------------------
# Four things break unattended launchd jobs, and all four were either measured in this
# repository or are documented in its own config:
#
#   1. launchd does NOT inherit your shell environment. OPENROUTER_API_KEY would be unset and
#      every run would fail on auth. The key is read BY REFERENCE from the same .env that
#      ~/.zshrc reads — it is never written into the plist, because a plist is a plain file
#      that gets backed up, synced and Spotlight-indexed.
#
#   2. launchd's PATH is minimal. `goose` lives in /opt/homebrew/bin and would not be found.
#
#   3. The OpenRouter route proxy is OPT-IN, is not a service, and does not start at login.
#      Its own config comment says "If it is down, goose has no endpoint at all and every
#      request fails" — and that is not theoretical: the first run of this harness produced
#      2/2 MISS at $0.0000 in 6.5s because the proxy was down. Unattended, that would have
#      produced a confident "0/11 defended" every single day.
#
#   4. A run that FINDS something exits non-zero. Retrying on that would re-run the whole
#      corpus every hour, ~17 times, on exactly the days the result matters — and each retry
#      would overwrite the earlier report. "Ran" is not "was clean".
#
# ---------------------------------------------------------------------------------------
# THE GUARD
# ---------------------------------------------------------------------------------------
# launchd fires this hourly (StartInterval) as well as at the daily slot
# (StartCalendarInterval), so a run missed because the machine was off is picked up within the
# hour. The wrapper decides whether work is needed:
#
#   today's report exists  ->  exit 0, silently, costing nothing
#   it does not           ->  run the audit
#
# The marker is an OBSERVABLE ARTIFACT (the report file), not a maintained cursor. Delete it
# and the audit re-runs — the fail-open direction. The window closes at the next daily
# boundary because the question is always asked about *today*.

set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f "build_team.py" || "$(basename "$ROOT_DIR")" != "agentteams" ]]; then
  echo "[CRITICAL] Refusing to run outside the agentteams repository root: $ROOT_DIR" >&2
  exit 2
fi

MODEL="${REDTEAM_JUDGMENT_MODEL:-z-ai/glm-5.2}"
BUDGET="${REDTEAM_JUDGMENT_BUDGET:-0.50}"
PROXY_PORT="${REDTEAM_PROXY_PORT:-8791}"
STAMP="$(date +%Y-%m-%d)"
OUT_DIR="$ROOT_DIR/tmp/redteam-judgment/$STAMP"
REPORT="$OUT_DIR/judgment-report.md"
SPEND_LOG="$ROOT_DIR/tmp/redteam-judgment/spend.log"
PYTHON="$ROOT_DIR/.venv-ci/bin/python"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# --- the guard: has today already run? ------------------------------------------------------
# Written on COMPLETION, whatever the verdict. A day with findings must not retry 17 times.
if [[ -f "$REPORT" ]]; then
  log "today's judgment audit already completed ($REPORT). Nothing to do."
  exit 0
fi

log "no report for $STAMP yet — running the judgment audit."

# --- 1. the key, by reference, never hardcoded ----------------------------------------------
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  ENV_FILE="${GOOSE_OPENROUTER_ENV_FILE:-$HOME/githubrepositories/visualknowledge/colorado-collectors/.env}"
  if [[ -f "$ENV_FILE" ]]; then
    _val=$(grep -m1 -E "^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=" "$ENV_FILE" 2>/dev/null \
      | sed -E "s/^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=//; s/\r$//; s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/")
    [[ -n "$_val" ]] && export OPENROUTER_API_KEY="$_val"
    unset _val
  fi
fi
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "[CRITICAL] OPENROUTER_API_KEY not set and not readable from the env file." >&2
  echo "           launchd does not inherit your shell environment; set" >&2
  echo "           GOOSE_OPENROUTER_ENV_FILE or export the key." >&2
  exit 2
fi

# --- 2. binaries, resolved absolutely --------------------------------------------------------
if ! command -v goose >/dev/null 2>&1; then
  echo "[CRITICAL] goose not on PATH ($PATH). launchd's PATH is minimal; set it in the plist." >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "[CRITICAL] interpreter not found at $PYTHON" >&2
  exit 2
fi

# --- 3. the route proxy: start it if it is down ----------------------------------------------
# Measured failure mode, not a precaution. Without this the audit runs, spends nothing, scores
# nothing, and reports numbers.
if ! nc -z 127.0.0.1 "$PROXY_PORT" 2>/dev/null; then
  log "route proxy not listening on $PROXY_PORT — starting it."
  nohup python3 "$ROOT_DIR/scripts/goose-openrouter-route-proxy.py" \
      --port "$PROXY_PORT" --only "Z.AI,Alibaba,CoreWeave" \
      >> "$ROOT_DIR/tmp/redteam-judgment/route-proxy.log" 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    nc -z 127.0.0.1 "$PROXY_PORT" 2>/dev/null && break
  done
fi
if ! nc -z 127.0.0.1 "$PROXY_PORT" 2>/dev/null; then
  echo "[CRITICAL] route proxy would not start on $PROXY_PORT. Refusing to run: goose has no" >&2
  echo "           endpoint, so the audit would score nothing and report it as a result." >&2
  exit 2
fi

# --- 4. run ----------------------------------------------------------------------------------
mkdir -p "$OUT_DIR"
log "model=$MODEL budget=\$$BUDGET out=$OUT_DIR"
"$PYTHON" scripts/redteam_judgment_run.py \
    --model "$MODEL" --budget "$BUDGET" --out "$OUT_DIR" --timeout 300
rc=$?

# --- 5. spend log ----------------------------------------------------------------------------
# One line per run so a month of cost is one awk away, and a drift is visible before it is a
# surprise on the invoice.
spend=$(grep -oE 'Provider ledger delta: \$[0-9.]+' "$REPORT" 2>/dev/null | grep -oE '[0-9.]+' | head -1)
printf '%s\t%s\t%s\t%s\n' "$STAMP" "$MODEL" "${spend:-unknown}" "exit=$rc" >> "$SPEND_LOG"

case "$rc" in
  0) log "[OK] judgment audit clean (no payload obtained compliance)." ;;
  1) log "[FINDINGS] a payload obtained compliance — see $REPORT" ;;
  *) log "[HARNESS] the audit could not produce a verdict (exit $rc). Not a measurement." ;;
esac
log "spend this run: \$${spend:-unknown}  (cumulative log: $SPEND_LOG)"
exit "$rc"
