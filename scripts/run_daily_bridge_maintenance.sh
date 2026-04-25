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
summary_rows=()
run_started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

run_noncritical() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  if "$@"; then
    echo "[OK] ${label}"
    summary_rows+=("| ${label} | OK |")
  else
    local rc=$?
    noncritical_failures=$((noncritical_failures + 1))
    echo "[WARN] ${label} failed (exit ${rc}); continuing with best judgement." >&2
    summary_rows+=("| ${label} | WARN (exit ${rc}) |")
  fi
}

echo "[INFO] Daily agentteams bridge maintenance started at ${run_started_at}"

SOURCE_DIR="$ROOT_DIR/.github/agents"
FALLBACK_SOURCE_DIR="$ROOT_DIR/examples/project-repositories/expected"
OUTPUT_ROOT="$ROOT_DIR"

if [[ ! -d "$SOURCE_DIR" ]]; then
  if [[ -d "$FALLBACK_SOURCE_DIR" ]]; then
    echo "[WARN] Primary source bridge directory missing; using fallback: $FALLBACK_SOURCE_DIR" >&2
    SOURCE_DIR="$FALLBACK_SOURCE_DIR"
  else
    echo "[CRITICAL] Source bridge directory not found: $SOURCE_DIR" >&2
    echo "[CRITICAL] Fallback source bridge directory not found: $FALLBACK_SOURCE_DIR" >&2
    exit 2
  fi
fi

if ! compgen -G "$SOURCE_DIR/*.agent.md" >/dev/null; then
  echo "[CRITICAL] Source bridge directory has no .agent.md files: $SOURCE_DIR" >&2
  exit 2
fi

targets=("copilot-cli" "claude")

for target in "${targets[@]}"; do
  run_noncritical \
    "Bridge refresh: copilot-vscode -> ${target}" \
    python build_team.py --bridge-from "$SOURCE_DIR" --framework "$target" --output "$OUTPUT_ROOT" --bridge-refresh

  run_noncritical \
    "Bridge check: copilot-vscode -> ${target}" \
    python build_team.py --bridge-from "$SOURCE_DIR" --framework "$target" --output "$OUTPUT_ROOT" --bridge-check
done

echo
if [[ "$noncritical_failures" -eq 0 ]]; then
  echo "[INFO] Daily agentteams bridge maintenance completed successfully."
else
  echo "[INFO] Daily agentteams bridge maintenance completed with ${noncritical_failures} non-critical warning(s)." >&2
fi

run_finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[INFO] Finished at ${run_finished_at}"

summary_dir="$ROOT_DIR/tmp/bridge-maintenance"
mkdir -p "$summary_dir"

summary_md="$summary_dir/summary.md"
{
  echo "# Bridge Maintenance Summary"
  echo
  echo "- started_at: ${run_started_at}"
  echo "- finished_at: ${run_finished_at}"
  echo "- warnings: ${noncritical_failures}"
  echo
  echo "| Step | Result |"
  echo "|---|---|"
  for row in "${summary_rows[@]}"; do
    echo "${row}"
  done
  echo
} > "$summary_md"

summary_json="$summary_dir/status.json"
{
  echo "{"
  echo "  \"finished_at\": \"${run_finished_at}\"," 
  echo "  \"warning_count\": ${noncritical_failures}"
  echo "}"
} > "$summary_json"

exit 0
