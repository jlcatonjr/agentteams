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

noncritical_failures=0

run_noncritical() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  if "$@"; then
    echo "[OK] ${label}"
  else
    local rc=$?
    noncritical_failures=$((noncritical_failures + 1))
    echo "[WARN] ${label} failed (exit ${rc}); continuing with best judgement." >&2
  fi
}

echo "[INFO] Daily agentteams security maintenance started at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

autoflags=(--self --yes --security-offline --security-no-nvd)

# 1) Non-destructive self update (merge only) to keep generated team in sync.
run_noncritical \
  "Self-team non-destructive update (--update --merge)" \
  python build_team.py "${autoflags[@]}" --update --merge

# 2) Security scan pass for generated agent files.
run_noncritical \
  "Self-team security scan" \
  python build_team.py "${autoflags[@]}" --merge --scan-security

# 3) Security-focused regression checks.
run_noncritical \
  "Security-focused test suite" \
  pytest -q \
    tests/test_build_team_security_gates.py \
    tests/test_security_refs.py \
    tests/test_scan.py \
    tests/test_render.py \
    tests/test_liaison_logs.py

# 4) Drift check for visibility (non-blocking in autonomous daily mode).
run_noncritical \
  "Self-team drift check" \
  python build_team.py "${autoflags[@]}" --check

echo
if [[ "$noncritical_failures" -eq 0 ]]; then
  echo "[INFO] Daily agentteams security maintenance completed successfully."
else
  echo "[INFO] Daily agentteams security maintenance completed with ${noncritical_failures} non-critical warning(s)." >&2
fi

echo "[INFO] Finished at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
exit 0
