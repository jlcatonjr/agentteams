#!/usr/bin/env bash
# L1 — READ-ONLY vulnerability inventory of one Linux host. Changes nothing.
# Ubuntu/Debian (apt) and Amazon Linux / RHEL / Fedora (dnf/yum).
#
#   ./remote.sh <host> L1-audit.sh                    # from your workstation (streamed, not copied)
#   ssh <host> 'sudo bash -s' < L1-audit.sh            # equivalent, by hand
#
# Prints human-readable OK/INFO/ACTION lines plus tab-separated records that
# `python -m agentteams.vulnscan inventory` judges on the workstation (so the host
# needs no feed access and this script embeds no vulnerability knowledge):
#   INV        ecosystem  name  version  location     installed PyPI / npm / Packagist packages
#   RUNTIME    kind  version  path  manager           node / php / python binaries
#   HOSTCHECK  severity  message                      OS patch state, reboot, exposure checks
#   IOC        message                                compromise indicator (from ioc-*.txt data)
# Exit: 0 nothing local to flag · 1 action needed · 4 compromise indicator.
#
# Indicator rules come from the ioc-*.txt data files beside remote.sh. remote.sh embeds
# them as a quoted heredoc (data, never executed) in AGENTTEAMS_IOC_DATA; one rule per line:
#   file  <path-glob>          the path exists
#   name  <file-name>          a file or directory with this name exists under the scan roots
#   env   <VAR=value>          some process has this in its environment
#   grep  <file-name> <regex>  a file with this name exists and its content matches regex

set -uo pipefail
declare -a _IF=() _IN=() _IE=() _IG=()
while IFS= read -r _line; do
  _line="${_line//$'\r'/}"; _line="${_line%%#*}"; read -r _kind _a _b <<< "$_line" || true   # CRLF data stays live
  case "$_kind" in
    file) [ -n "$_a" ] && _IF+=("$_a");; name) [ -n "$_a" ] && _IN+=("$_a");;
    env)  [ -n "$_a" ] && _IE+=("$_a");; grep) [ -n "$_a" ] && [ -n "$_b" ] && _IG+=("$_a:$_b");;
  esac
done <<< "${AGENTTEAMS_IOC_DATA:-}"
unset _line _kind _a _b

SCAN_ROOTS="/usr/lib /usr/lib64 /usr/local /opt /srv /var/www /home /root"
RC=0
flag(){ [ "$1" -gt "$RC" ] && RC=$1; }
ok(){   printf 'OK          %s\n' "$*"; }
info(){ printf 'INFO        %s\n' "$*"; }
act(){  printf 'ACTION      %s\n' "$*"; flag 1; }
check(){ printf 'HOSTCHECK\t%s\t%s\n' "$1" "$2"; act "$2"; }
ioc(){  printf 'IOC\t%s\n' "$*"; printf 'COMPROMISE  %s\n' "$*"; flag 4; }
sec(){  printf '\n== %s\n' "$*"; }
pkg_owner(){
  if command -v dpkg >/dev/null 2>&1; then dpkg -S "$1" 2>/dev/null | head -1 | cut -d: -f1
  elif command -v rpm >/dev/null 2>&1; then rpm -qf "$1" 2>/dev/null | grep -v 'not owned' | head -1; fi
}
manager_of(){  # how a binary was installed
  local b="$1" o
  case "$b" in */.nvm/*) echo nvm; return;; */.volta/*) echo volta; return;; */fnm/*) echo fnm; return;;
               */conda*/*|*/miniforge*/*) echo conda; return;; */.pyenv/*) echo pyenv; return;; esac
  o=$(pkg_owner "$b"); [ -n "$o" ] && { echo "pkg:$o"; return; }
  echo tarball
}

os_section(){
  sec "OS"
  [ -r /etc/os-release ] && . /etc/os-release
  echo "host: $(hostname)  os: ${PRETTY_NAME:-unknown}  kernel: $(uname -r)  arch: $(uname -m)  at: $(date -u +%FT%TZ)"
  if command -v apt-get >/dev/null 2>&1; then
    local stamp age n
    stamp=$(stat -c %Y /var/lib/apt/periodic/update-success-stamp 2>/dev/null || stat -c %Y /var/lib/apt/lists 2>/dev/null || echo 0)
    age=$(( ( $(date +%s) - stamp ) / 3600 ))
    [ "$age" -gt 26 ] && check LOW "apt package lists are ${age}h old — security-update counts below may be stale" || info "apt lists ${age}h old"
    n=$(apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst .*-security')
    [ "$n" -gt 0 ] && check HIGH "$n pending OS security package updates (L2-os-patch.sh)" || ok "no pending security updates (per cached lists)"
    info "$(apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst ') pending updates in total"
    [ -f /var/run/reboot-required ] && check MODERATE "reboot required to load patched packages: $(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)"
    if dpkg -s unattended-upgrades >/dev/null 2>&1 && systemctl is-enabled apt-daily-upgrade.timer >/dev/null 2>&1; then
      ok "unattended-upgrades installed and apt-daily-upgrade.timer enabled"
    else check MODERATE "no automatic security patching (unattended-upgrades not active)"; fi
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    local pm n; pm=$(command -v dnf || command -v yum)
    n=$($pm -q --cacheonly updateinfo list --security 2>/dev/null | grep -c .)
    [ "$n" -gt 0 ] && check HIGH "$n pending OS security advisories (L2-os-patch.sh)" || ok "no pending security advisories (per cached metadata)"
    if command -v needs-restarting >/dev/null 2>&1; then needs-restarting -r >/dev/null 2>&1 || check MODERATE "reboot required (needs-restarting -r)"; fi
    if systemctl is-enabled dnf-automatic.timer >/dev/null 2>&1 || systemctl is-enabled dnf-automatic-install.timer >/dev/null 2>&1; then
      ok "dnf-automatic timer enabled"
    else check MODERATE "no automatic security patching (dnf-automatic not enabled)"; fi
  else check MODERATE "unknown package manager — OS security patch state was not assessed (patch this host by hand)"; fi
}

runtime_section(){
  sec "Language runtimes"
  local b v
  for b in $( { command -v php; ls /usr/bin/php[0-9]* /usr/local/bin/php /opt/*/bin/php /opt/remi/*/root/usr/bin/php 2>/dev/null; } | xargs -r -n1 readlink -f 2>/dev/null | sort -u ); do
    [ -x "$b" ] || continue
    v=$("$b" -r 'echo PHP_VERSION;' 2>/dev/null) || continue
    printf 'RUNTIME\tphp\t%s\t%s\t%s\n' "$v" "$b" "$(manager_of "$b")"
  done
  for b in $( { which -a node nodejs 2>/dev/null; find $SCAN_ROOTS -xdev -maxdepth 8 -path '*/node_modules' -prune -o -type f -name node -path '*/bin/node' -print 2>/dev/null; } | xargs -r -n1 readlink -f 2>/dev/null | sort -u ); do
    [ -x "$b" ] || continue
    v=$("$b" -v 2>/dev/null) || continue
    printf 'RUNTIME\tnode\t%s\t%s\t%s\n' "${v#v}" "$b" "$(manager_of "$b")"
  done
  for b in $( { ls /usr/bin/python3* /usr/local/bin/python3* /opt/*/bin/python3 /root/*conda*/bin/python3 /home/*/*conda*/bin/python3 2>/dev/null; } | grep -E 'python3(\.[0-9]+)?$' | xargs -r -n1 readlink -f 2>/dev/null | sort -u ); do
    v=$("$b" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null) || continue
    printf 'RUNTIME\tpython\t%s\t%s\t%s\n' "$v" "$b" "$(manager_of "$b")"
  done
  ps -eo pid=,user=,comm= 2>/dev/null | awk '$3 ~ /^(node|nodejs|php-fpm.*|python3.*|gunicorn|uwsgi)$/' | while read -r pid user comm; do
    info "running pid $pid ($user) $comm → $(readlink -f /proc/$pid/exe 2>/dev/null)"; done | sort -u -k5 | head -40
}

packages_section(){
  sec "Installed packages (judged on the workstation)"
  local sp d f n
  for sp in $(find $SCAN_ROOTS -xdev -maxdepth 8 -path '*/node_modules' -prune -o -type d \( -name site-packages -o -name dist-packages \) -print 2>/dev/null | sort -u); do
    n=0
    for d in "$sp"/*.dist-info "$sp"/*.egg-info; do
      [ -e "$d" ] || continue
      f="$d/METADATA"; [ -f "$f" ] || f="$d/PKG-INFO"; [ -f "$f" ] || continue
      awk -v loc="$sp" 'BEGIN{FS=": "} /^Name: /{n=$2} /^Version: /{v=$2} /^$/{exit} END{if(n!=""&&v!="") printf "INV\tPyPI\t%s\t%s\t%s\n", n, v, loc}' "$f" | tr -d '\r'
      n=$((n+1))
    done
    [ "$n" -gt 0 ] && info "$sp: $n packages"
  done
  local manifests
  manifests=$(find /var/www /srv /opt /home /root -xdev -maxdepth 7 \( -path '*/node_modules/*/node_modules' -prune \) -o \( -path '*/node_modules/.package-lock.json' -o -path '*/vendor/composer/installed.json' \) -print 2>/dev/null)
  [ -n "$manifests" ] || return 0
  if ! command -v python3 >/dev/null 2>&1; then   # unread manifests are a gap in the inventory, never a clean result
    check MODERATE "python3 missing — $(printf '%s\n' "$manifests" | grep -c .) npm/composer manifest(s) were not inventoried"
    return
  fi
  for f in $manifests; do
    python3 -I - "$f" <<'PY'
import json, sys
f = sys.argv[1]
try:
    d = json.load(open(f))
except (OSError, ValueError) as e:
    print(f"INFO        unreadable {f}: {e}"); sys.exit()
if f.endswith(".package-lock.json"):
    loc = f[: -len("/node_modules/.package-lock.json")]
    for k, v in (d.get("packages") or {}).items():
        if k and "version" in v and not v.get("link"):
            print(f"INV\tnpm\t{v.get('name') or k.split('node_modules/')[-1]}\t{v['version']}\t{loc}")
else:
    loc = f[: -len("/vendor/composer/installed.json")]
    for p in (d.get("packages") if isinstance(d, dict) else d) or []:
        print(f"INV\tPackagist\t{p['name']}\t{str(p.get('version', '')).lstrip('v')}\t{loc}")
PY
  done
}

services_section(){
  sec "Services and listeners"
  local u ex
  for u in $(systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'); do
    ex=$(systemctl show -p ExecStart --value "$u" 2>/dev/null | grep -oE 'argv\[\]=[^;]+' | head -1)
    echo "$ex" | grep -qiE 'python|gunicorn|uwsgi|uvicorn|node|npm|pm2|php|java|docker|ruby' && info "$u → ${ex#argv[]=}"
  done
  command -v ss >/dev/null 2>&1 && ss -ltnpH 2>/dev/null | awk '{printf "INFO        listen %-22s %s\n", $4, $6}'
  if command -v docker >/dev/null 2>&1; then
    docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null | sed 's/^/INFO        container: /'
    info "container images are refreshed with L5-containers.sh"
  fi
}

ioc_section(){
  sec "Compromise indicators (${#_IF[@]} path, ${#_IN[@]} name, ${#_IE[@]} env, ${#_IG[@]} content rules)"
  # ${a[@]+"${a[@]}"}: an empty array is "unbound" under set -u before bash 4.4 (RHEL 7, Amazon Linux 2)
  local g p name pat e any=0
  for g in ${_IF[@]+"${_IF[@]}"}; do for p in $g; do [ -e "$p" ] && { ioc "indicator path present: $p"; any=1; }; done; done
  for e in ${_IE[@]+"${_IE[@]}"}; do grep -qsl -- "$e" /proc/[0-9]*/environ 2>/dev/null && { ioc "a process has $e in its environment"; any=1; }; done
  if [ ${#_IN[@]} -gt 0 ] || [ ${#_IG[@]} -gt 0 ]; then
    local expr=() first=1
    for name in ${_IN[@]+"${_IN[@]}"} ${_IG[@]+"${_IG[@]%%:*}"}; do
      [ $first -eq 1 ] && first=0 || expr+=(-o); expr+=(-name "$name")
    done
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      for name in ${_IN[@]+"${_IN[@]}"}; do [ "$(basename "$p")" = "$name" ] && { ioc "indicator file: $p"; any=1; }; done
      for g in ${_IG[@]+"${_IG[@]}"}; do
        [ "$(basename "$p")" = "${g%%:*}" ] && grep -qiE -- "${g#*:}" "$p" 2>/dev/null && { ioc "indicator content in $p"; any=1; }
      done
    done < <(find $SCAN_ROOTS -xdev -maxdepth 9 \( "${expr[@]}" \) 2>/dev/null)
  fi
  [ "$any" -eq 0 ] && ok "no compromise indicators matched"
}

docroot_section(){
  command -v nginx >/dev/null 2>&1 || command -v apache2ctl >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1 || return 0
  sec "Secret-looking files inside web document roots"
  local r hits roots
  roots=$( { nginx -T 2>/dev/null | awk '$1=="root"{gsub(";","",$2); print $2}'; \
             { apache2ctl -S 2>/dev/null; httpd -S 2>/dev/null; } | awk '/DocumentRoot/{gsub("\"","",$NF); print $NF}'; } | sort -u)
  for r in $roots; do
    [ -d "$r" ] || continue
    hits=$(find "$r" -xdev -maxdepth 4 -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name 'token.json' \
            -o -name 'client_secret*.json' -o -name '*.sqlite*' -o -name '*.db' -o -name 'id_rsa*' -o -name 'id_ed25519*' \) \
            -not -path '*/.git/*' 2>/dev/null | head -20)
    if [ -n "$hits" ]; then
      check HIGH "secret-looking files under web root $r — confirm the server denies them (request them from outside; expect 403/404)"
      echo "$hits" | sed 's/^/              /'
    else ok "nothing secret-looking under $r"; fi
  done
}

main(){
  os_section; runtime_section; packages_section; services_section; ioc_section; docroot_section
  sec "RESULT"
  case "$RC" in
    0) echo "RESULT clean (package/runtime verdicts come from the workstation report)";;
    1) echo "RESULT action-needed";;
    4) echo "RESULT COMPROMISE — isolate the host, stop the payload, rebuild, rotate every credential it could reach";;
  esac
  return "$RC"
}

main "$@" < /dev/null
