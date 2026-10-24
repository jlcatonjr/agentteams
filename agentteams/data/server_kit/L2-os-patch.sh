#!/usr/bin/env bash
# L2 — OS security patching (covers distro PHP, distro/NodeSource Node, OpenSSL,
# kernel, Python system packages). DRY RUN by default.
#
#   ./remote.sh <host> L2-os-patch.sh                         # show what would change
#   ./remote.sh <host> L2-os-patch.sh --apply                 # install SECURITY updates only
#   ./remote.sh <host> L2-os-patch.sh --apply --all           # install all updates
#   ./remote.sh <host> L2-os-patch.sh --apply --restart-services   # + restart services using old libs
#   ./remote.sh <host> L2-os-patch.sh --apply --reboot        # + reboot in 2 min if the kernel needs it
#
# Keeps existing config files on conflicts (--force-confold). Never touches sshd
# config. Service restarts and reboots happen only when asked.
set -uo pipefail

APPLY=false; ALL=false; RESTART=false; REBOOT=false
for a in "$@"; do case "$a" in
  --apply) APPLY=true;; --all) ALL=true;; --restart-services) RESTART=true;; --reboot) REBOOT=true;;
  *) echo "unknown option: $a" >&2; exit 2;; esac; done

say(){ printf '\n== %s\n' "$*"; }
run(){ if $APPLY; then echo "+ $*"; "$@"; else echo "WOULD  $*"; fi; }
# Never auto-restart these: losing them mid-session strands the operator.
SKIP_RESTART='^(ssh|sshd|systemd-.*|dbus.*|getty@.*|serial-getty@.*|user@.*)\.service$'

apt_flow(){
  export DEBIAN_FRONTEND=noninteractive
  if $APPLY; then
    say "Refreshing package lists"
    apt-get update -q || { echo "FAIL apt-get update"; return 1; }
  else
    say "Package lists (dry run: cached lists, no refresh — nothing on the host is written)"
  fi
  say "Pending updates"
  apt-get -s upgrade | awk '/^Inst /{s=($0 ~ /-security/)?"SECURITY":"        "; print s, $2, $3, $4}' | tr -d '[]('
  local O=(-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
  if $ALL; then
    say "Installing ALL updates"; run apt-get -y "${O[@]}" upgrade
  elif command -v unattended-upgrade >/dev/null 2>&1; then
    say "Installing SECURITY updates (unattended-upgrade)"
    if $APPLY; then echo "+ unattended-upgrade -v"; unattended-upgrade -v; else echo "WOULD  unattended-upgrade -v   (installs the SECURITY lines listed above)"; fi
  else
    say "Installing SECURITY updates (no unattended-upgrades: security-origin packages only)"
    local pk; pk=$(apt-get -s upgrade | awk '/^Inst .*-security/{print $2}' | tr '\n' ' ')
    [ -n "$pk" ] && run apt-get -y "${O[@]}" install --only-upgrade $pk || echo "none"
  fi
  say "Services still using replaced libraries"
  local svcs=""
  if command -v needrestart >/dev/null 2>&1; then
    svcs=$(NEEDRESTART_MODE=l needrestart -b -r l 2>/dev/null | awk -F': ' '/^NEEDRESTART-SVC/{print $2}')
  else echo "(install needrestart for an exact list: apt-get install needrestart)"; fi
  restart_list "$svcs"
  say "Reboot"
  if [ -f /var/run/reboot-required ]; then reboot_if_asked "$(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)"; else echo "not required"; fi
}

dnf_flow(){
  local pm; pm=$(command -v dnf || command -v yum)
  say "Pending security advisories"
  if $APPLY; then $pm -q --refresh updateinfo list --security 2>/dev/null || true
  else $pm -q --cacheonly updateinfo list --security 2>/dev/null || true; fi
  if $ALL; then say "Installing ALL updates"; run "$pm" -y upgrade
  else say "Installing SECURITY updates"; run "$pm" -y upgrade --security; fi
  say "Services still using replaced libraries"
  local svcs=""
  if command -v needs-restarting >/dev/null 2>&1; then svcs=$(needs-restarting -s 2>/dev/null | tr '\n' ' ')
  else echo "(dnf install dnf-utils for needs-restarting)"; fi
  restart_list "$svcs"
  say "Reboot"
  if command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; then reboot_if_asked "core libraries/kernel updated"
  else echo "not required (or unknown)"; fi
}

restart_list(){
  local s any=false
  for s in $1; do
    any=true
    if echo "$s" | grep -qE "$SKIP_RESTART"; then echo "SKIP   $s (restart manually / reboot)"; continue; fi
    if $RESTART; then run systemctl restart "$s"; sleep 2; systemctl is-active --quiet "$s" && echo "OK     $s active" || echo "FAIL   $s NOT active — check: journalctl -u $s -n 50"
    else echo "NEEDS  $s (re-run with --restart-services)"; fi
  done
  $any || echo "none"
}

reboot_if_asked(){
  echo "REQUIRED: $1"
  if $REBOOT && $APPLY; then echo "+ shutdown -r +2 'security patching'"; shutdown -r +2 "security patching (agentteams server kit)"
  else echo "re-run with --apply --reboot, or let the host's configured automatic-reboot window handle it"; fi
}

main(){
  echo "host: $(hostname)  $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-}")  mode: $($APPLY && echo APPLY || echo DRY-RUN)"
  if command -v apt-get >/dev/null 2>&1; then apt_flow
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then dnf_flow
  else echo "unsupported package manager"; return 2; fi
  $APPLY || printf '\nDRY RUN — nothing installed. Re-run with --apply.\n'
}

main "$@" < /dev/null
