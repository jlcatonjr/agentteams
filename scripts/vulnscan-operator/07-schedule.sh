#!/usr/bin/env bash
# 07 — install the daily job (walkthrough Step 3): launchd on macOS, a systemd user timer or cron on Linux.
#
# Shows the dry run first (the job file and the commands), asks you to confirm, then installs it with --apply
# and prints `schedule status`. The job runs `daily` with this install's interpreter; while agent.enabled is
# false it only scans (and notifies). It bakes in PATH, HOME and vulnscan's location variables, never any
# other AGENTTEAMS_* value. Run it from a terminal whose PATH finds docker, git, gh and ssh.
#
# Usage:   HOUR=7 MINUTE=30 bash scripts/vulnscan-operator/07-schedule.sh
#          bash scripts/vulnscan-operator/07-schedule.sh --remove
set -euo pipefail

PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-schedule.XXXXXX")"; trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

if [ "${1:-}" = "--remove" ]; then
  "${VS[@]}" schedule remove --apply
  "${VS[@]}" schedule status
  exit 0
fi

for tool in docker git gh; do command -v "$tool" >/dev/null || echo "Warning: $tool is not on PATH here; the job inherits this PATH" >&2; done
args=(--hour "${HOUR:-7}" --minute "${MINUTE:-30}")
echo "== dry run"
"${VS[@]}" schedule install "${args[@]}"
printf '\nInstall this job now? [y/N] '
read -r ans
case "$ans" in y|Y|yes) ;; *) echo "not installed"; exit 0;; esac
"${VS[@]}" schedule install "${args[@]}" --apply
echo
"${VS[@]}" schedule status
