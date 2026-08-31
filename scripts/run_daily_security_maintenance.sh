#!/usr/bin/env bash
set -u
set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Hard scope guard: this script is for agentteams only.
if [[ ! -f "build_team.py" || ! -d "agentteams" || "$(basename "$ROOT_DIR")" != "agentteams" ]]; then
  echo "[CRITICAL] Refusing to run outside agentteams repository root: $ROOT_DIR" >&2
  exit 2
fi

if ! command -v python >/dev/null 2>&1; then
  echo "[CRITICAL] Python not found in PATH." >&2
  exit 2
fi

run_critical() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  if "$@"; then
    echo "[OK] ${label}"
  else
    local rc=$?
    echo "[CRITICAL] ${label} failed (exit ${rc}); stopping security maintenance." >&2
    exit "$rc"
  fi
}

echo "[INFO] Daily agentteams security maintenance started at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

autoflags=(--self --yes --security-offline --security-no-nvd)
update_flags=(--self --yes --security-no-nvd)

SECURITY_SNAPSHOT="$ROOT_DIR/.github/agents/references/security-vulnerability-watch.json"
refresh_decision="$(
  python - "$SECURITY_SNAPSHOT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

snapshot = Path(sys.argv[1])
if not snapshot.exists():
    print("refresh:missing-snapshot")
    raise SystemExit(0)

try:
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("refresh:invalid-snapshot")
    raise SystemExit(0)

generated_at = str(payload.get("generated_at") or "").strip()
if not generated_at:
    print("refresh:missing-generated-at")
    raise SystemExit(0)

try:
    generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
except ValueError:
    print("refresh:invalid-generated-at")
    raise SystemExit(0)

age_delta_hours = (datetime.now(timezone.utc) - generated_dt).total_seconds() / 3600.0
if age_delta_hours < -(5.0 / 60.0):
    print(f"refresh:future-timestamp:{age_delta_hours:.2f}")
elif age_delta_hours >= 24.0:
    print(f"refresh:stale:{age_delta_hours:.2f}")
else:
    print(f"offline:fresh:{max(age_delta_hours, 0.0):.2f}")
PY
)"

if [[ "$refresh_decision" == refresh:* ]]; then
  reason="${refresh_decision#refresh:}"
  echo "[INFO] Security references require refresh (${reason}); running update without --security-offline."
else
  reason="${refresh_decision#offline:}"
  echo "[INFO] Security references are fresh (${reason}); running update with --security-offline."
  update_flags+=(--security-offline)
fi

# 1) Non-destructive self update (merge only) to keep generated team in sync.
#
# T3.2: --shrink-policy=halt makes the self-team strict about fence content.
# A destructive shrink (CVEs dropped, paths lost) blocks the write and exits
# critical. Recovery if you hit a legitimate shrink: run ONCE with
# `--shrink-policy=allow` (or warn), commit the resulting state, then this
# script returns to halt enforcement on the next run. Consumer repos are
# unaffected — they get the default warn behaviour.
# Plan: references/plans/T3-2-self-team-halt-policy-2026-05-25.plan.md
run_critical \
  "Self-team non-destructive update (--update --merge --shrink-policy=halt)" \
  python build_team.py "${update_flags[@]}" --update --merge --shrink-policy=halt

# 2) Security scan pass for generated agent files.
run_critical \
  "Self-team security scan" \
  python build_team.py "${autoflags[@]}" --merge --scan-security

# 3) Security-focused regression checks.
run_critical \
  "Security-focused test suite" \
  pytest -q \
    tests/test_build_team_security_gates.py \
    tests/test_security_refs.py \
    tests/test_scan.py \
    tests/test_render.py \
    tests/test_liaison_logs.py

# 4) Drift check is a critical gate in autonomous daily mode.
run_critical \
  "Self-team drift check" \
  python build_team.py "${autoflags[@]}" --check

echo
echo "[INFO] Daily agentteams security maintenance completed successfully."

echo "[INFO] Finished at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
exit 0
