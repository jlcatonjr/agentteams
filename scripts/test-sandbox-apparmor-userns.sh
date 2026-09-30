#!/usr/bin/env bash
# Operator tool (root): find which AppArmor change lets Claude Code's Linux sandbox run on an
# Ubuntu host with kernel.apparmor_restrict_unprivileged_userns=1. Each candidate is applied
# TEMPORARILY, probed with a real sandboxed Claude Code command, and reverted. A candidate is
# persisted only with `--persist <documented|sysctl>`, and only if its probe PASSED.
#
# Why (measured 2026-09-30, Claude Code 2.1.251): Ubuntu's /etc/apparmor.d/bwrap-userns-restrict
# moves every process bwrap launches into `unpriv_bwrap`, which has `audit deny capability`.
# Claude Code runs its `apply-seccomp` helper from an anonymous memfd INSIDE bwrap, and the helper
# needs a capability in its nested user namespace, so it is refused ("caller must provide
# CAP_SYS_ADMIN"). Consequences:
#   * a profile on the `claude` binary cannot reach the helper (it runs under bwrap's profile,
#     and a memfd has no path to attach one to);
#   * a local/unpriv_bwrap `allow capability` cannot work either: an AppArmor deny beats an allow;
#   * `sandbox.enableWeakerNestedSandbox` does not help (it only swaps /proc).
# Candidates tested:
#   documented  Claude Code's documented fix (code.claude.com/docs/en/sandboxing, "Set up Linux
#               and WSL2"): an unconfined `profile bwrap /usr/bin/bwrap { userns, }`. It collides
#               BY NAME with the `profile bwrap` in Ubuntu's bwrap-userns-restrict, so persisting it
#               disables that file. Scope: HOST-WIDE. bwrap and everything it launches (e.g.
#               flatpak) run without that AppArmor confinement.
#   sysctl      kernel.apparmor_restrict_unprivileged_userns=0. Relaxes the restriction for every
#               unprivileged process on the host, and may not help at all, because bwrap's profile
#               still confines its children.
#
# Usage:
#   sudo bash scripts/test-sandbox-apparmor-userns.sh                      # test both; revert both
#   sudo bash scripts/test-sandbox-apparmor-userns.sh --persist documented # keep it, if it PASSED
#   sudo bash scripts/test-sandbox-apparmor-userns.sh --persist sysctl     # keep it, if it PASSED
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 2; }
USER_NAME="${SUDO_USER:?run via sudo from your normal account}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PERSIST=""
if [ "${1:-}" = "--persist" ]; then
  PERSIST="${2:-}"
  case "$PERSIST" in documented|sysctl) ;; *) echo "--persist expects documented|sysctl" >&2; exit 2 ;; esac
fi
CLAUDE_BIN="$(readlink -f "$USER_HOME/.local/bin/claude" 2>/dev/null || true)"
[ -x "$CLAUDE_BIN" ] || { echo "claude CLI not found at $USER_HOME/.local/bin/claude" >&2; exit 2; }
RUN_AS=(sudo -H -u "$USER_NAME" env "PATH=$USER_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "HOME=$USER_HOME")

SYSCTL=kernel.apparmor_restrict_unprivileged_userns
UBUNTU_PROFILE=/etc/apparmor.d/bwrap-userns-restrict
DOC_PROFILE=/etc/apparmor.d/bwrap
DISABLE_LINK=/etc/apparmor.d/disable/bwrap-userns-restrict
[ -f "$UBUNTU_PROFILE" ] || { echo "no $UBUNTU_PROFILE: this host does not have the Ubuntu restriction this tool targets" >&2; exit 2; }
command -v apparmor_parser >/dev/null || { echo "apparmor_parser not found" >&2; exit 2; }
orig_sysctl="$(sysctl -n "$SYSCTL")"

doc_profile_text() {
  printf '%s\n' 'abi <abi/4.0>,' 'include <tunables/global>' \
    'profile bwrap /usr/bin/bwrap flags=(unconfined) {' '  userns,' '  include if exists <local/bwrap>' '}'
}

revert() {  # restore the host exactly; every step runs even if an earlier one fails
  set +e
  sysctl -qw "$SYSCTL=$orig_sysctl"
  apparmor_parser -r "$UBUNTU_PROFILE" >/dev/null 2>&1   # reload Ubuntu's bwrap + unpriv_bwrap
}
trap revert EXIT INT TERM HUP

probe() {  # a real sandboxed Claude Code command, run as the operator; prints PASS/FAIL
  local p esc inroot=0 escaped=0 switch_ok=0
  p="$("${RUN_AS[@]}" mktemp -d "$USER_HOME/.agentteams-aaprobe-XXXX")"
  esc="$("${RUN_AS[@]}" mktemp -d "$USER_HOME/.agentteams-aaescape-XXXX")"
  "${RUN_AS[@]}" bash -c "
    set -e; cd '$p'; mkdir -p .claude/agents/references/authorized-verify-keys .claude/hooks
    echo '{\"enforce_decision_signing\": true}' > .claude/agents/references/agent-privilege.json
    touch .claude/hooks/constitutional-gate.py
    python3 -c \"import json,sys; sys.path.insert(0,'$REPO'); from agentteams.frameworks._sandbox_emit import _build_sandbox_block as b; json.dump({'sandbox':b(['$p'])},open('.claude/settings.json','w'))\"
    timeout 200 '$CLAUDE_BIN' -p \"Run this exact bash command and report ONLY its exact output: echo in > inroot.txt; echo out > '$esc/x.txt' 2>/dev/null || true; echo x > .claude/agents/references/agent-privilege.json 2>/dev/null || true; echo DONE\" \
      --model claude-haiku-4-5-20251001 --permission-mode acceptEdits --allowedTools Bash >/dev/null 2>&1 || true"
  # Inspect and clean up as the user: never let root follow paths the agent could have planted.
  "${RUN_AS[@]}" test -f "$p/inroot.txt" && inroot=1
  "${RUN_AS[@]}" test -e "$esc/x.txt" && escaped=1
  "${RUN_AS[@]}" grep -q '"enforce_decision_signing": true' "$p/.claude/agents/references/agent-privilege.json" && switch_ok=1
  "${RUN_AS[@]}" rm -rf "$p" "$esc"
  if [ "$inroot" -eq 1 ] && [ "$escaped" -eq 0 ] && [ "$switch_ok" -eq 1 ]; then
    echo "PASS (in-root write ok, escape denied, switch intact)"
  else
    echo "FAIL (inroot=$inroot escaped=$escaped switch_intact=$switch_ok)"
  fi
}

echo "Baseline ($SYSCTL=$orig_sysctl): $(probe)"

echo; echo "Candidate documented: Claude Code's unconfined bwrap profile (temporary)"
doc_profile_text | apparmor_parser -r   # from stdin: nothing written to a guessable path
res_documented="$(probe)"; echo "  -> $res_documented"
apparmor_parser -r "$UBUNTU_PROFILE"

echo; echo "Candidate sysctl: $SYSCTL=0 (temporary)"
sysctl -qw "$SYSCTL=0"
res_sysctl="$(probe)"; echo "  -> $res_sysctl"
sysctl -qw "$SYSCTL=$orig_sysctl"

case "$PERSIST" in
  documented)
    case "$res_documented" in PASS*) ;; *) echo; echo "NOT persisted: the documented candidate did not PASS." >&2; exit 1 ;; esac
    [ -e "$DOC_PROFILE" ] && cp -p "$DOC_PROFILE" "$DOC_PROFILE.agentteams-bak.$(date +%s)"
    doc_profile_text > "$DOC_PROFILE"
    ln -sf "$UBUNTU_PROFILE" "$DISABLE_LINK"
    trap - EXIT INT TERM HUP
    apparmor_parser -R "$UBUNTU_PROFILE" >/dev/null 2>&1 || true
    apparmor_parser -r "$DOC_PROFILE"
    echo; echo "PERSISTED documented: $DOC_PROFILE installed; Ubuntu's bwrap-userns-restrict disabled HOST-WIDE via $DISABLE_LINK."
    echo "Undo: rm $DOC_PROFILE $DISABLE_LINK && apparmor_parser -r $UBUNTU_PROFILE" ;;
  sysctl)
    case "$res_sysctl" in PASS*) ;; *) echo; echo "NOT persisted: the sysctl candidate did not PASS." >&2; exit 1 ;; esac
    trap - EXIT INT TERM HUP
    apparmor_parser -r "$UBUNTU_PROFILE" >/dev/null 2>&1 || true
    echo "$SYSCTL=0" > /etc/sysctl.d/60-agentteams-userns.conf; sysctl -qw "$SYSCTL=0"
    echo; echo "PERSISTED sysctl: /etc/sysctl.d/60-agentteams-userns.conf (host-wide). Undo: remove it, then sysctl -w $SYSCTL=1" ;;
  *) echo; echo "Nothing persisted (both candidates reverted). Re-run with --persist documented|sysctl to keep one that PASSED." ;;
esac
