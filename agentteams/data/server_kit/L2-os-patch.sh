#!/usr/bin/env bash
# L2 — OS security patching (covers distro PHP, distro/NodeSource Node, OpenSSL,
# kernel, Python system packages). DRY RUN by default.
#
#   ./remote.sh <host> L2-os-patch.sh                         # show what would change
#   ./remote.sh <host> L2-os-patch.sh --apply                 # install SECURITY updates only
#   ./remote.sh <host> L2-os-patch.sh --apply --all           # install all updates
#   ./remote.sh <host> L2-os-patch.sh --apply --restart-services   # + restart services using old libs
#   ./remote.sh <host> L2-os-patch.sh --apply --reboot        # + reboot in 2 min if the kernel needs it
#   ./remote.sh <host> L2-os-patch.sh --level [--restart-unit U ...]            # server-apply l2-security: the plan
#   ./remote.sh <host> L2-os-patch.sh --level --apply --expect-digest SHA ...   # install exactly that plan
#
# --level (server-apply l2-security): SECURITY updates only, from the cached package lists (no refresh, so the plan
# the operator confirmed is what installs), each at its planned version; restarts only --restart-unit units that
# still use replaced libraries, each health-checked (active + its TCP ports listening again); never reboots, never
# --all. Exit 5: the plan on the host differs from --expect-digest (nothing changed); 7: a restarted unit failed
# its health check (distro packages are not rolled back: the host is stopped for the operator).
#
# Keeps existing config files on conflicts (--force-confold). Never touches sshd
# config. Service restarts and reboots happen only when asked.
set -uo pipefail

APPLY=false; ALL=false; RESTART=false; REBOOT=false; LEVEL=false; EXPECT=""; RUNITS=()
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --all) ALL=true;; --restart-services) RESTART=true;; --reboot) REBOOT=true;;
  --level) LEVEL=true;;
  --expect-digest|--restart-unit)
    case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac
    if [ "$1" = --expect-digest ]; then EXPECT="$2"; else RUNITS+=("$2"); fi; shift;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
if $LEVEL; then
  if $ALL || $RESTART || $REBOOT; then echo "refused: --level never takes --all, --restart-services or --reboot" >&2; exit 2; fi
  if $APPLY && ! [[ $EXPECT =~ ^[0-9a-f]{64}$ ]]; then echo "refused: --level --apply needs --expect-digest" >&2; exit 2; fi
elif [ -n "$EXPECT" ] || [ ${#RUNITS[@]} -gt 0 ]; then echo "refused: --expect-digest/--restart-unit need --level" >&2; exit 2; fi
TAB=$(printf '\t')

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

# ---- level mode (server-apply l2-security)
# BEGIN plan_digest (identical in run-host.sh and every level script)
plan_digest(){
  tr -d '\r' | LC_ALL=C grep "^PLAN$TAB" | LC_ALL=C sed -E 's/[[:space:]]+/ /g; s/ $//' | LC_ALL=C sort -u \
    | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | awk '{print $1}'
}
# END plan_digest
# BEGIN health helpers (identical in L2, L4 and L6)
unit_ports(){   # TCP ports the unit's main process listens on, one per line, sorted
  local pid; pid=$(systemctl show -p MainPID --value "$1" 2>/dev/null)
  [ -n "$pid" ] && [ "$pid" != 0 ] || return 0
  ss -Hltnp 2>/dev/null | awk -v p="pid=$pid," 'index($0, p) {n=split($4, a, ":"); print a[n]}' | sort -un
}
unit_healthy(){   # $1 unit, $2 ports it listened on before: active 3 checks running, then each port listening again
  local u="$1" before="$2" i ok=0 now p
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 2
    systemctl is-active --quiet "$u" || { echo "FAIL   $u not active"; return 1; }
    ok=$((ok+1)); [ "$ok" -ge 3 ] && break
  done
  for i in 1 2 3 4 5 6 7 8 9 10; do
    now=" $(unit_ports "$u" | tr '\n' ' ') "; missing=""
    for p in $before; do case "$now" in *" $p "*) ;; *) missing="$missing $p";; esac; done
    [ -z "$missing" ] && { echo "OK     $u active${before:+, listening on $(echo $before)}"; return 0; }
    sleep 2
  done
  echo "FAIL   $u no longer listens on:$missing"; return 1
}
# END health helpers
level_plan(){   # PLAN lines: the security updates the cached lists offer, each at its new version
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get -s upgrade 2>/dev/null \
      | awk -v t="$TAB" '/^Inst / && /-security/ && $3 ~ /^\[/ {o=$3; n=$4; gsub(/[][()]/, "", o); gsub(/[][()]/, "", n); print "PLAN" t "os" t "apt" t $2 t o t n}'
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    local pm; pm=$(command -v dnf || command -v yum)
    "$pm" -q --cacheonly updateinfo list --security 2>/dev/null \
      | awk -v t="$TAB" 'NF >= 3 {print "PLAN" t "os" t "dnf" t $NF t $1}'
  else echo "unsupported package manager" >&2; return 2; fi
}
level_prestate(){
  if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f '${Package} ${Version}\n' 2>/dev/null | sed "s/^/PRESTATE${TAB}dpkg${TAB}/"
  elif command -v rpm >/dev/null 2>&1; then rpm -qa --qf '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>/dev/null | sed "s/^/PRESTATE${TAB}rpm${TAB}/"; fi
}
has(){ local x="$1" y; shift; for y in "$@"; do [ "$x" = "$y" ] && return 0; done; return 1; }
level_main(){
  echo "host: $(hostname)  level: l2-security  mode: $($APPLY && echo APPLY || echo PLAN)"
  local plan; plan=$(level_plan) || return 2
  [ -n "$plan" ] && printf '%s\n' "$plan" || echo "no security updates pending"
  if ! $APPLY; then level_prestate; return 0; fi
  local got; got=$(printf '%s\n' "$plan" | plan_digest)
  [ "$got" = "$EXPECT" ] || { echo "DIGEST MISMATCH: plan $got, confirmed $EXPECT — nothing changed"; return 5; }
  [ -n "$plan" ] || { echo "nothing to install"; return 0; }
  local O=(-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold) specs=()
  if printf '%s\n' "$plan" | grep -q "^PLAN${TAB}os${TAB}apt${TAB}"; then
    while IFS="$TAB" read -r _ _ _ pkg _ new; do specs+=("$pkg=$new"); done <<< "$plan"
    echo "+ apt-get -y install --only-upgrade ${specs[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get -y "${O[@]}" install --only-upgrade "${specs[@]}" || return 3
  else
    while IFS="$TAB" read -r _ _ _ nevra _; do specs+=("$nevra"); done <<< "$plan"
    local pm; pm=$(command -v dnf || command -v yum)
    echo "+ $pm -y upgrade ${specs[*]}"; "$pm" -y upgrade "${specs[@]}" || return 3
  fi
  say "Services still using replaced libraries"
  local svcs="" s before
  if command -v needrestart >/dev/null 2>&1; then
    svcs=$(NEEDRESTART_MODE=l needrestart -b -r l 2>/dev/null | awk -F': ' '/^NEEDRESTART-SVC/{print $2}')
  elif command -v needs-restarting >/dev/null 2>&1; then svcs=$(needs-restarting -s 2>/dev/null | tr '\n' ' '); fi
  for s in $svcs; do
    if ! has "$s" ${RUNITS[@]+"${RUNITS[@]}"} || echo "$s" | grep -qE "$SKIP_RESTART"; then
      echo "NEEDS  $s restart pending (not a signed restart unit)"; continue
    fi
    before=$(unit_ports "$s")
    echo "+ systemctl restart $s"; systemctl restart "$s" || { echo "FAIL   restart $s"; return 7; }
    unit_healthy "$s" "$before" || return 7
  done
  if [ -f /var/run/reboot-required ] || { command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; }; then
    echo "REBOOT pending (server-apply never reboots)"
  fi
  return 0
}

main(){
  if $LEVEL; then level_main; return $?; fi
  echo "host: $(hostname)  $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-}")  mode: $($APPLY && echo APPLY || echo DRY-RUN)"
  if command -v apt-get >/dev/null 2>&1; then apt_flow
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then dnf_flow
  else echo "unsupported package manager"; return 2; fi
  $APPLY || printf '\nDRY RUN — nothing installed. Re-run with --apply.\n'
}

main "$@" < /dev/null
