#!/usr/bin/env bash
# Operator tool (root): find which AppArmor change lets Claude Code's Linux sandbox run on
# this host. Each candidate is tested TEMPORARILY and reverted. Nothing persists unless you pass
# --persist <sysctl|override> after reading the results.
#
# Why (measured 2026-09-30, Ubuntu, kernel.apparmor_restrict_unprivileged_userns=1): Claude Code
# 2.1.251 runs its `apply-seccomp` helper from an anonymous memfd INSIDE bwrap. Ubuntu's
# bwrap-userns-restrict profile moves every process bwrap launches into `unpriv_bwrap`, which has
# `audit deny capability`, so the helper's nested-userns setgroups write is refused ("caller must
# provide CAP_SYS_ADMIN"). A profile on the `claude` binary does NOT reach that process, and a
# memfd has no path to attach a profile to, so the only levers are:
#   A) the sysctl (may or may not help, because the bwrap profile still transitions children), or
#   B) a local override granting capabilities to unpriv_bwrap. This widens EVERY bwrap-launched
#      process on the host (e.g. flatpak), but only inside the user namespace bwrap created.
#   C) Claude Code's DOCUMENTED fix (code.claude.com/docs/en/sandboxing, "Set up Linux and WSL2"):
#      a `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }`. On this host it collides by
#      NAME with Ubuntu's own `profile bwrap` in bwrap-userns-restrict: whichever loads last wins,
#      and at boot the directory loads in alphabetical order, so Ubuntu's file would override a new
#      /etc/apparmor.d/bwrap. The test loads C directly. `--persist documented` keeps it durably by
#      disabling Ubuntu's bwrap-userns-restrict (a symlink in /etc/apparmor.d/disable) and installing
#      the documented profile. Scope: bwrap and everything it launches run unconfined by AppArmor
#      (the same host-wide widening for bwrap users as B, but the vendor-documented form).
# `sandbox.enableWeakerNestedSandbox` was tested too and does NOT avoid the failure (it only swaps
# the fresh /proc for the parent's).
#
# Usage:
#   sudo bash scripts/test-sandbox-apparmor-userns.sh              # test A, C, B; revert all
#   sudo bash scripts/test-sandbox-apparmor-userns.sh --persist documented # keep C (vendor-documented)
#   sudo bash scripts/test-sandbox-apparmor-userns.sh --persist override   # keep B
#   sudo bash scripts/test-sandbox-apparmor-userns.sh --persist sysctl     # keep A
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 2; }
USER_NAME="${SUDO_USER:?run via sudo from your normal account}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_BIN="$(readlink -f "$USER_HOME/.local/bin/claude" 2>/dev/null || true)"
[ -x "$CLAUDE_BIN" ] || { echo "claude CLI not found at $USER_HOME/.local/bin/claude" >&2; exit 2; }
RUN_AS=(sudo -H -u "$USER_NAME" env "PATH=$USER_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "HOME=$USER_HOME")
PERSIST="${2:-}"; [ "${1:-}" = "--persist" ] || PERSIST=""
SYSCTL=kernel.apparmor_restrict_unprivileged_userns
LOCAL=/etc/apparmor.d/local/unpriv_bwrap
PROFILE=/etc/apparmor.d/bwrap-userns-restrict
MARK="# agentteams-sandbox-test"

orig_sysctl="$(sysctl -n "$SYSCTL")"
had_local=0; [ -f "$LOCAL" ] && had_local=1 && cp -p "$LOCAL" "$LOCAL.agentteams-bak"

revert() {
  sysctl -qw "$SYSCTL=$orig_sysctl"
  if [ "$had_local" -eq 1 ]; then mv -f "$LOCAL.agentteams-bak" "$LOCAL"; else rm -f "$LOCAL"; fi
  apparmor_parser -r "$PROFILE" 2>/dev/null || true
}

probe() {  # run a real sandboxed Claude Code command as the operator; print PASS/FAIL
  local p esc out
  p="$("${RUN_AS[@]}" mktemp -d "$USER_HOME/.agentteams-aaprobe-XXXX")"
  esc="$("${RUN_AS[@]}" mktemp -d "$USER_HOME/.agentteams-aaescape-XXXX")"
  "${RUN_AS[@]}" bash -c "
    set -e; cd '$p'; mkdir -p .claude/agents/references/authorized-verify-keys .claude/hooks
    echo '{\"enforce_decision_signing\": true}' > .claude/agents/references/agent-privilege.json
    touch .claude/hooks/constitutional-gate.py
    python3 -c \"import json,sys; sys.path.insert(0,'$REPO'); from agentteams.frameworks._sandbox_emit import _build_sandbox_block as b; json.dump({'sandbox':b(['$p'])},open('.claude/settings.json','w'))\"
    timeout 200 '$CLAUDE_BIN' -p \"Run this exact bash command and report ONLY its exact output: echo in > inroot.txt; echo out > '$esc/x.txt' 2>/dev/null || true; echo x > .claude/agents/references/agent-privilege.json 2>/dev/null || true; echo DONE\" \
      --model claude-haiku-4-5-20251001 --permission-mode acceptEdits --allowedTools Bash >/dev/null 2>&1 || true"
  local inroot=0 escaped=0 switch_ok=0
  [ -f "$p/inroot.txt" ] && inroot=1
  [ -f "$esc/x.txt" ] && escaped=1
  grep -q '"enforce_decision_signing": true' "$p/.claude/agents/references/agent-privilege.json" && switch_ok=1
  rm -rf "$p" "$esc"
  if [ "$inroot" -eq 1 ] && [ "$escaped" -eq 0 ] && [ "$switch_ok" -eq 1 ]; then
    echo "PASS (in-root write ok, escape denied, switch intact)"
  else
    echo "FAIL (inroot=$inroot escaped=$escaped switch_intact=$switch_ok)"
  fi
}

trap revert EXIT
echo "Baseline ($SYSCTL=$orig_sysctl): $(probe)"

echo; echo "Candidate A: $SYSCTL=0 (temporary)"
sysctl -qw "$SYSCTL=0"
res_a="$(probe)"; echo "  -> $res_a"
sysctl -qw "$SYSCTL=$orig_sysctl"

echo; echo "Candidate C: Claude Code's documented unconfined bwrap profile (temporary)"
DOC=/tmp/agentteams-bwrap-doc.profile
printf '%s\n' 'abi <abi/4.0>,' 'include <tunables/global>' 'profile bwrap /usr/bin/bwrap flags=(unconfined) {' '  userns,' '}' > "$DOC"
apparmor_parser -r "$DOC"
res_c="$(probe)"; echo "  -> $res_c"
apparmor_parser -r "$PROFILE"   # restore Ubuntu's bwrap + unpriv_bwrap
rm -f "$DOC"

echo; echo "Candidate B: local override granting capabilities to unpriv_bwrap (temporary)"
{ [ "$had_local" -eq 1 ] && cat "$LOCAL.agentteams-bak"; echo "$MARK"; echo "  allow capability sys_admin,"; echo "  allow capability setgid,"; echo "  allow capability setuid,"; } > "$LOCAL"
apparmor_parser -r "$PROFILE"
res_b="$(probe)"; echo "  -> $res_b"

case "$PERSIST" in
  documented)
    trap - EXIT; revert
    ln -sf "$PROFILE" /etc/apparmor.d/disable/bwrap-userns-restrict
    apparmor_parser -R "$PROFILE" 2>/dev/null || true
    printf '%s\n' 'abi <abi/4.0>,' 'include <tunables/global>' 'profile bwrap /usr/bin/bwrap flags=(unconfined) {' '  userns,' '  include if exists <local/bwrap>' '}' > /etc/apparmor.d/bwrap
    apparmor_parser -r /etc/apparmor.d/bwrap
    echo; echo "PERSISTED C: /etc/apparmor.d/bwrap installed; Ubuntu's bwrap-userns-restrict disabled via /etc/apparmor.d/disable/. Undo: rm /etc/apparmor.d/bwrap /etc/apparmor.d/disable/bwrap-userns-restrict && apparmor_parser -r $PROFILE" ;;
  sysctl)
    trap - EXIT; revert
    echo "$SYSCTL=0" > /etc/sysctl.d/60-agentteams-userns.conf; sysctl -qw "$SYSCTL=0"
    echo; echo "PERSISTED A: /etc/sysctl.d/60-agentteams-userns.conf (remove it + reboot/sysctl to undo)" ;;
  override)
    trap - EXIT; sysctl -qw "$SYSCTL=$orig_sysctl"; rm -f "$LOCAL.agentteams-bak"
    echo; echo "PERSISTED B: $LOCAL (delete the lines after '$MARK' and run: apparmor_parser -r $PROFILE to undo)" ;;
  *) echo; echo "Nothing persisted (both candidates reverted). Re-run with --persist documented|override|sysctl to keep one." ;;
esac
