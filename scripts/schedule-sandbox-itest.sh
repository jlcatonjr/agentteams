#!/usr/bin/env bash
# Operator action (follow-up #6): re-run the Claude Code sandbox product itest weekly, so a Claude Code
# upgrade that changes the undocumented sandbox behaviour agentteams relies on is caught. A pass
# refreshes ~/.cache/agentteams/claude-sandbox-itest.json, which `agentteams --check-wiring` reads
# (advisory only).
#
# OPERATOR-ONLY. An agent must never run --apply: a timer is a persistence mechanism.
#   * dry run by default (prints the unit files and the cost note); --apply installs; --remove uninstalls
#   * systemd USER units only; refuses root / sudo; never enables linger (it runs while you are logged in)
#   * no secrets in the units: the itest uses the Claude Code login you already have
#   * bounded: one run a week, Persistent=false, randomized start, 30 min hard timeout
#   * logs to ~/.cache/agentteams/sandbox-itest.log (mode 0600); the environment is never logged
#
# COST: each run makes ~40 short `claude -p` calls (incl. the paired Write-tool probes) on the itest model (default
# claude-haiku-4-5-20251001; override with CLAUDE_SANDBOX_ITEST_MODEL in the service). That is
# typically well under US$0.50 per run, on your own account.
#
# Usage:  bash scripts/schedule-sandbox-itest.sh [--apply|--remove] [--repo DIR]
set -euo pipefail
MODE=dry; REPO="$(cd "$(dirname "$0")/.." && pwd)"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) MODE=apply; shift ;;
    --remove) MODE=remove; shift ;;
    --repo) REPO="$(cd "${2:?--repo DIR}" && pwd)"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" -eq 0 ] || [ -n "${SUDO_USER:-}" ]; then
  echo "refusing: run as your own user, not root/sudo (systemd --user units only)." >&2; exit 2
fi
[ -f "$REPO/tests/test_os_sandbox_product_enforcement.py" ] || { echo "not an agentteams checkout: $REPO" >&2; exit 2; }
PY="$(command -v python3)"
CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] || { echo "claude CLI not found on PATH" >&2; exit 2; }
# Every value embedded in a unit file: no whitespace/newline, quotes, shell metacharacters or `%`
# (a systemd specifier), so nothing can inject a directive or expand.
for v in "$REPO" "$PY" "$CLAUDE_BIN"; do
  case "$v" in *[[:space:]\'\"\$\`\\%\;\&\|\<\>]*|"") echo "refusing: unsafe path for a unit file: $v" >&2; exit 2 ;; esac
done
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
NAME=agentteams-sandbox-itest

SERVICE="[Unit]
Description=agentteams Claude Code sandbox product itest (advisory)

[Service]
Type=oneshot
WorkingDirectory=$REPO
Environment=RUN_CLAUDE_SANDBOX_ITEST=1
Environment=CLAUDE_CLI=$CLAUDE_BIN
TimeoutStartSec=1800
UMask=0077
ExecStart=/bin/sh -c 'mkdir -p %h/.cache/agentteams && touch %h/.cache/agentteams/sandbox-itest.log && chmod 600 %h/.cache/agentteams/sandbox-itest.log && exec $PY -m pytest -q -p no:cacheprovider tests/test_os_sandbox_product_enforcement.py >> %h/.cache/agentteams/sandbox-itest.log 2>&1'
"
TIMER="[Unit]
Description=Weekly agentteams sandbox itest

[Timer]
OnCalendar=weekly
RandomizedDelaySec=6h
Persistent=false

[Install]
WantedBy=timers.target
"

case "$MODE" in
  dry)
    echo "DRY RUN. Would write $UNIT_DIR/$NAME.service:"; echo "$SERVICE"
    echo "and $UNIT_DIR/$NAME.timer:"; echo "$TIMER"
    echo "Cost: ~40 short claude -p calls per weekly run on your account (see header)."
    echo "Re-run with --apply to install (your user only; no linger), or --remove to uninstall." ;;
  apply)
    command -v systemctl >/dev/null || { echo "systemctl not found" >&2; exit 2; }
    mkdir -p "$UNIT_DIR"
    umask 077
    printf '%s' "$SERVICE" > "$UNIT_DIR/$NAME.service"
    printf '%s' "$TIMER" > "$UNIT_DIR/$NAME.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME.timer"
    echo "Installed. Check: systemctl --user list-timers $NAME.timer ; log: ~/.cache/agentteams/sandbox-itest.log" ;;
  remove)
    systemctl --user disable --now "$NAME.timer" 2>/dev/null || true
    rm -f "$UNIT_DIR/$NAME.service" "$UNIT_DIR/$NAME.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed." ;;
esac
