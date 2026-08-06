#!/usr/bin/env bash
#
# run_daily_redteam_audit.sh — the standing red-team audit, once a day.
#
# Runs the probe battery, the review, the remediation skeleton and the six phase-6 self-audit
# checks that evaluate the red team itself. It MEASURES AND REPORTS. It never remediates:
# an unattended job that writes remediation code is a larger risk than the one it closes.
#
# Procedure:  references/redteam-audit.procedure.md
# Engine:     agentteams/redteam/
# Workflow:   .github/workflows/redteam-audit.yml
#
# ---------------------------------------------------------------------------------------
# THREE OUTCOMES, KEPT DISTINGUISHABLE
# ---------------------------------------------------------------------------------------
#   0  clean            — no live exploit, no self-audit finding, live agent tree untouched
#   1  findings         — a measured attack is live, or phase 6 found a defect in the red team
#   2  harness broken   — a control probe failed, the probe module would not import, a corpus
#                         claim no longer matches the scanner, the run modified the live agent
#                         tree, or the audit DIED WITH A TRACEBACK
#
# The last clause is why this script exists rather than the workflow calling the CLI directly.
# `agentteams/redteam/` contains no `except` clauses at all: a probe that raises propagates,
# because catching it to synthesise an exit code would add the broad `except` CH-24 ratchets
# against and would recreate the PROBE-ERROR row the battery's own comment warns about — it
# would let a battery of broken probes finish and report all-clear. So the classification
# happens here, in the shell, where a traceback death is visible as a non-zero exit that is
# neither 0 nor 1.
#
# Indeterminate is not a pass. Code 2 outranks code 1.

set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Hard scope guard, mirroring run_daily_security_maintenance.sh: this script is for
# agentteams only, and must never run against a consumer repository that happens to have
# vendored it.
if [[ ! -f "build_team.py" || ! -d "agentteams" || "$(basename "$ROOT_DIR")" != "agentteams" ]]; then
  echo "[CRITICAL] Refusing to run outside agentteams repository root: $ROOT_DIR" >&2
  exit 2
fi

PYTHON="${PYTHON:-python}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "[CRITICAL] Python not found in PATH." >&2
  exit 2
fi

PROBES="${REDTEAM_PROBE_MODULE:-tests.constitutional_redteam_battery}"
STAMP="$(date -u +"%Y-%m-%d")"
REPORT_DIR="${REDTEAM_REPORT_DIR:-$ROOT_DIR/tmp/redteam/$STAMP}"

echo "[INFO] Standing red-team audit started at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[INFO] probe module : $PROBES"
echo "[INFO] report dir   : $REPORT_DIR"
echo

"$PYTHON" build_team.py --redteam --redteam-probes "$PROBES" --redteam-report "$REPORT_DIR"
rc=$?

echo
case "$rc" in
  0)
    echo "[OK] Red-team audit clean: no live exploit, no self-audit finding, live agent tree untouched."
    ;;
  1)
    echo "[FINDINGS] The red-team audit found something. See $REPORT_DIR/selfaudit.md and" >&2
    echo "           $REPORT_DIR/remediation.plan.md. Take it through phases 3-5 and 7:" >&2
    echo "           audit the plan, rehearse each fix against an ISOLATED COPY of a real" >&2
    echo "           target, and assert the complete expected end state - not the absence" >&2
    echo "           of an error." >&2
    ;;
  2)
    echo "[HARNESS-BROKEN] The instrument failed, not the target. These results say nothing" >&2
    echo "                 about whether the constitution holds, and must NOT be read as a" >&2
    echo "                 clean run. Fix the harness, then re-run." >&2
    ;;
  *)
    # A traceback, a killed process, a missing interpreter. Anything that is not a verdict is
    # classified as a broken harness, never as a pass.
    echo "[HARNESS-BROKEN] The audit exited $rc without producing a verdict (a traceback, a" >&2
    echo "                 timeout, or a killed process). Indeterminate is not a pass." >&2
    rc=2
    ;;
esac

echo "[INFO] Finished at $(date -u +"%Y-%m-%dT%H:%M:%SZ") with exit $rc"
exit "$rc"
