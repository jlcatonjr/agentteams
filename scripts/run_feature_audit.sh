#!/usr/bin/env bash
#
# run_feature_audit.sh — the standing feature audit.
#
# Verifies that features documented in docs_src/api-reference/feature-inventory.md still
# function, using the machine-readable registry at references/feature-registry.csv.
#
# NOT named run_daily_*. Cadence lives in the workflow, not here — the same decision
# recorded in scripts/run_redteam_audit.sh, which was renamed away from
# run_daily_redteam_audit.sh when its cadence moved: a script whose NAME asserts a cadence
# it no longer has is stale content in the most load-bearing place there is (Rule 7).
#
# It MEASURES AND REPORTS. It never remediates: an unattended job that writes fixes is a
# larger risk than the one it closes.
#
# Procedure: references/feature-audit.procedure.md
# Engine:    agentteams/feature_audit.py
# Registry:  references/feature-registry.csv
#
# ---------------------------------------------------------------------------------------
# THREE OUTCOMES, KEPT DISTINGUISHABLE
# ---------------------------------------------------------------------------------------
#   0  clean            — registry agrees with the inventory, and every executed proof passed
#   1  findings         — a proof failed, the registry drifted from the inventory, or a
#                         requested tier executed ZERO proofs
#   2  harness broken   — the registry is malformed or missing, the engine would not import,
#                         or the audit DIED WITH A TRACEBACK
#
# "A tier that executed zero proofs" is deliberately a FINDING and not clean. An
# all-UNPROVEN registry would otherwise satisfy every structural check and exit 0 — success
# declared over nothing proven, which is the exact defect this audit exists to detect in
# other people's checks.
#
# UNREACHABLE is not a failure. A live-tier dependency that is down is somebody else's
# outage, reported but never gating. Conversely a live proof must assert positive content:
# agentteams/research is degrade-don't-raise, so "no exception" would pass on an empty
# response — a proof that cannot fail.
#
# Indeterminate is not a pass. Code 2 outranks code 1.

set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Hard scope guard, mirroring run_redteam_audit.sh: this script is for agentteams only and
# must never run against a consumer repository that happens to have vendored it.
if [[ ! -f "build_team.py" || ! -d "agentteams" || "$(basename "$ROOT_DIR")" != "agentteams" ]]; then
  echo "[CRITICAL] Refusing to run outside agentteams repository root: $ROOT_DIR" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
TIERS="${FEATURE_AUDIT_TIERS:-unit}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
REPORT_DIR="${FEATURE_AUDIT_REPORT_DIR:-tmp/feature-audit/${STAMP}}"
mkdir -p "$REPORT_DIR"
REPORT="${REPORT_DIR}/report.txt"

# The live probes are env-gated so ordinary CI stays deterministic. If the live tier is
# requested we must enable them, or every live proof SKIPS — and a skipped proof is now
# reported as FAIL (it proved nothing), which would read as a broken feature.
case "$TIERS" in
  *live*) export FEATURE_AUDIT_LIVE=1 ;;
esac

echo "[INFO] feature audit — tiers: ${TIERS}"
echo "[INFO] report: ${REPORT}"

set +e
"$PYTHON_BIN" -m agentteams.feature_audit --tiers "$TIERS" >"$REPORT" 2>&1
RC=$?
set -e

cat "$REPORT"

# The engine returns 0/1/2 itself. Any OTHER code is a death we did not classify — a
# traceback, an import failure, a signal. That is harness-broken, not "no findings".
case "$RC" in
  0) echo "[CLEAN] every executed proof passed and the registry matches the inventory" ;;
  1) echo "[FINDINGS] see the report above" ;;
  2) echo "[HARNESS BROKEN] the audit could not produce a verdict" >&2 ;;
  *) echo "[HARNESS BROKEN] audit exited ${RC} — unclassified death" >&2; RC=2 ;;
esac

exit "$RC"
