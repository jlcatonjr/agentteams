#!/usr/bin/env bash
#
# install-sandbox-deps.sh — install and check the Linux dependencies of Claude Code's sandbox.
#
# Operator action (uses sudo). Claude Code's Linux/WSL2 sandbox needs bubblewrap (`bwrap`) AND
# `socat` on PATH. Measured 2026-09-30 (Claude Code 2.1.251): with `socat` missing, a sandbox
# block WITHOUT `failIfUnavailable: true` printed "Sandbox disabled ... Commands will run WITHOUT
# sandboxing" and ran every command unconfined. agentteams now emits `failIfUnavailable: true`, so
# the same host refuses to start instead. This script makes the sandbox start.
#
# What it does:
#   1. installs bubblewrap and/or socat via apt-get or dnf when missing (sudo);
#   2. prints their versions;
#   3. smoke-tests unprivileged bwrap;
#   4. DIAGNOSES — never changes — kernel.apparmor_restrict_unprivileged_userns.
#
# It never edits sysctls, AppArmor profiles or any settings.json.
#
# Usage:  bash scripts/install-sandbox-deps.sh
# Docs:   docs_src/api-reference/workspace-privilege-scoping.md ("Fail closed")
set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
  echo "install-sandbox-deps.sh: Linux only (macOS uses Seatbelt, which needs no install)." >&2
  exit 2
fi

need=()
command -v bwrap >/dev/null 2>&1 || need+=(bubblewrap)
command -v socat >/dev/null 2>&1 || need+=(socat)

if [ ${#need[@]} -eq 0 ]; then
  echo "bubblewrap and socat are already installed."
else
  echo "Installing: ${need[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y "${need[@]}"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y "${need[@]}"
  else
    echo "No apt-get or dnf found. Install these packages with your package manager: ${need[*]}" >&2
    exit 1
  fi
fi

echo
echo "bwrap: $(bwrap --version 2>&1 | head -1)"
echo "socat: $(socat -V 2>&1 | sed -n 2p)"

echo
if bwrap --ro-bind / / --dev /dev --proc /proc true 2>/dev/null; then
  echo "bwrap smoke test: OK (unprivileged user namespaces work for bwrap)"
else
  echo "bwrap smoke test: FAILED — unprivileged user namespaces appear to be blocked for bwrap." >&2
  smoke_failed=1
fi

# Diagnose only. Claude Code's sandbox applies a seccomp filter from a NESTED user namespace.
# With this AppArmor restriction on (Ubuntu 23.10+ default), that step fails with
# "apply-seccomp ... nested userns is capability-restricted", so every sandboxed command fails
# CLOSED even though the plain bwrap smoke test above passes.
knob=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
echo
if [ -r "$knob" ]; then
  val="$(cat "$knob")"
  echo "kernel.apparmor_restrict_unprivileged_userns = $val"
  if [ "$val" = "1" ]; then
    cat <<'EOF'
  RESTRICTED. Claude Code's sandboxed commands will fail closed on this host (its seccomp step
  cannot run in a nested user namespace). This script does not change it. Two operator options:

  (a) An AppArmor profile for the claude binary that grants `userns,` (narrow: only claude, and
      the processes it starts, regain unprivileged user namespaces). Example, as root:
        /etc/apparmor.d/claude-code:
          abi <abi/4.0>,
          include <tunables/global>
          profile claude-code /path/to/claude flags=(unconfined) {
            userns,
          }
        apparmor_parser -r /etc/apparmor.d/claude-code
      The path must match the real binary (resolve ~/.local/bin/claude with `readlink -f`), and
      an update that moves the binary silently undoes it.

  (b) sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0 (persist in /etc/sysctl.d/).
      Simple and survives claude updates, but it removes the restriction for EVERY unprivileged
      process on the host, re-exposing the kernel user-namespace attack surface the default
      exists to reduce.

  Trade-off: (a) keeps the host hardening and scopes the exception to one binary but must track
  that binary's path; (b) is one line but weakens the whole host.
EOF
  else
    echo "  Not restricted: Claude Code's nested-userns seccomp step can run."
  fi
else
  echo "kernel.apparmor_restrict_unprivileged_userns: not present (no AppArmor userns restriction)."
fi

if [ "${smoke_failed:-0}" = "1" ]; then
  exit 1
fi

echo
echo "Next: verify the product arm from the agentteams repo (it FAILS unless the sandbox is operational):"
echo "  RUN_CLAUDE_SANDBOX_ITEST=1 python -m pytest -p no:cacheprovider tests/test_os_sandbox_product_enforcement.py -v"
