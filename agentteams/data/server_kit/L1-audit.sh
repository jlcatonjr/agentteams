#!/usr/bin/env bash
# L1 — READ-ONLY vulnerability inventory of one Linux host. Changes nothing it manages.
# Run as root, the package tools it queries may still touch their own state: `apt-cache policy` runs with its
# binary caches off (Dir::Cache::pkgcache= and srcpkgcache=), so it writes no cache file; `dnf -C repoquery`
# reads the cache only (-C) but may append a line to dnf's own log (/var/log/dnf*.log). Nothing else is written.
# Ubuntu/Debian (apt) and Amazon Linux / RHEL / Fedora (dnf/yum).
#
#   ./remote.sh <host> L1-audit.sh                    # from your workstation (streamed, not copied)
#   ssh <host> 'sudo bash -s' < L1-audit.sh            # equivalent, by hand
#
# Prints human-readable OK/INFO/ACTION lines plus tab-separated records that
# `python -m agentteams.vulnscan inventory` judges on the workstation (so the host
# needs no feed access and this script embeds no vulnerability knowledge):
#   INV        ecosystem  name  version  location     installed PyPI / npm / Packagist packages
#   RUNTIME    kind  version  path  manager           runtimes and server software (kinds below)
#   OSREL      ID  VERSION_ID  PRETTY_NAME            /etc/os-release, parsed (never sourced); ID "unknown" if absent
#   ESM        esm-infra  esm-apps                    Ubuntu only: enabled|disabled|unknown, read from
#                                                     /var/lib/ubuntu-advantage/status.json (`pro` is never run)
#   HOSTCHECK  severity  message                      OS patch state, reboot, exposure checks
#   IOC        message                                compromise indicator (from ioc-*.txt data)
# RUNTIME kinds: php node python ruby go java java-corretto dotnet nginx openssl libressl postgresql mysql
# mariadb redis docker (libressl is reported, not judged). Each probe only prints a version: no service is
# started, stopped or reloaded, nothing is written (but see above), nothing leaves the host, and each runs under `timeout 10`
# when `timeout` exists. docker reads the local socket only (DOCKER_HOST is pinned to it).
# RUNTIME manager: nvm / volta / fnm / conda / pyenv / tarball, or for a dpkg/rpm-owned binary
#   pkg:<package>[:universe]   the installed version comes from an allowlisted distro archive (distro_host);
#                              ":universe" marks Ubuntu universe/multiverse (security fixes need esm-apps)
#   repo:<host>:<package>      any other origin: a third-party repository (nginx.org, PGDG, docker, PPA, ...)
#   repo:unknown:<package>     origin unreadable, a local .deb/.rpm, or a version no repository serves any more
# repo: installs are judged against upstream versions on the workstation; pkg: installs are left to distro updates.
# Exit: 0 nothing local to flag · 1 action needed · 4 compromise indicator.
#
# Secret-bearing surfaces are never read or printed by default: no process environment (/proc/*/environ),
# no unit file or `systemctl cat`, no journal, no .env or credential file contents; a running service is shown
# by its executable path only (never its arguments, which can carry inline secrets). `env` indicator rules
# need process environments, so they run only with --deep (an operator decision per host).
#
# AGENTTEAMS_L1_ROOT (tests only) prefixes the two files read for OSREL and ESM; unset on a real host.
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
# Run a probe under a 10 s limit where `timeout` exists, so a hung binary cannot stall the audit.
tmo(){ if command -v timeout >/dev/null 2>&1; then timeout 10 "$@" </dev/null; else "$@" </dev/null; fi; }
# The distro archives whose packages get the distribution's security fixes (plan N5, Revision 3). A package from
# any other host, or from no readable host, is judged against upstream versions on the workstation.
distro_host(){
  case "$1" in
    *archive.ubuntu.com|security.ubuntu.com|esm.ubuntu.com|*ports.ubuntu.com) return 0;;
    deb.debian.org|security.debian.org) return 0;;
    amazonlinux*.amazonaws.com|al2023-repos-*.amazonaws.com|cdn.amazonlinux.com) return 0;;   # Amazon Linux repos
    cdn.redhat.com|*.rockylinux.org|*.almalinux.org) return 0;;
  esac
  return 1
}
apt_sources(){  # "host component" per archive serving the installed version: the `***` block of apt-cache policy
  tmo apt-cache -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= policy "$1" 2>/dev/null | awk '
    /^ \*\*\* /{inb=1; next}
    inb && $1 ~ /^-?[0-9]+$/ && ($2 ~ /^\// || $2 ~ /:/) {   # a source line, not the next version
      if ($2 ~ /^\//) next                                   # /var/lib/dpkg/status: installed, origin unknown
      h = "unknown"
      if ($2 ~ /^[a-z+]+:\/\//) { h = $2; sub(/^[a-z+]+:\/\//, "", h); sub(/[\/:].*$/, "", h); if (h == "") h = "unknown" }
      n = split($3, s, "/"); print tolower(h), (n > 1 ? s[2] : "-"); next }
    inb {exit}'
}
rpm_repo(){  # repo id the installed rpm came from (cache only, never the network), or nothing
  local r=""
  if command -v dnf >/dev/null 2>&1; then
    r=$(tmo dnf -C -q repoquery --installed --qf '%{from_repo}\n' "$1" 2>/dev/null | head -1)
  elif command -v yumdb >/dev/null 2>&1; then
    r=$(tmo yumdb get from_repo "$1" 2>/dev/null | awk -F' = ' '/from_repo/{print $2; exit}')
  fi
  case "$r" in ""|@System|System|"<unknown>"|@commandline|commandline|"(none)") return 0;; esac
  echo "${r#@}"
}
repo_host(){  # host of repo id $1's first baseurl/mirrorlist/metalink in /etc/yum.repos.d, dnf vars filled in
  local h f n v
  h=$(awk -v id="[$1]" '/^\[/{ins = ($1 == id); next}
       ins && /^[ \t]*(baseurl|mirrorlist|metalink)[ \t]*=/{sub(/^[^=]*=[ \t]*/, ""); split($0, a, /[ \t,]+/); print a[1]; exit}' \
       /etc/yum.repos.d/*.repo 2>/dev/null)
  case "$h" in *://*) h="${h#*://}"; h="${h%%/*}"; h="${h%%:*}";; *) return 0;; esac
  for f in /etc/dnf/vars/* /etc/yum/vars/*; do
    [ -f "$f" ] || continue
    n=$(basename "$f"); v=$(head -1 "$f" 2>/dev/null)
    h="${h//\$\{$n\}/$v}"; h="${h//\$$n/$v}"
  done
  case "$h" in *'$'*|"") return 0;; esac
  printf '%s\n' "$h" | tr 'A-Z' 'a-z'
}
pkg_origin(){  # pkg:<name>[:universe] for an allowlisted distro archive; else repo:<host|unknown>:<name>
  local p="$1" h c bad="" uni="" any=0 r v
  if command -v dpkg >/dev/null 2>&1; then
    if command -v apt-cache >/dev/null 2>&1; then
      while read -r h c; do
        [ -n "$h" ] || continue
        any=1; distro_host "$h" || { [ -n "$bad" ] || bad="$h"; }
        case "$c" in universe|multiverse) uni=":universe";; esac
      done < <(apt_sources "$p")
    fi
  elif command -v rpm >/dev/null 2>&1; then
    r=$(rpm_repo "$p"); h=""
    [ -n "$r" ] && h=$(repo_host "$r")
    if [ -n "$h" ]; then any=1; distro_host "$h" || bad="$h"
    else
      v=$(rpm -q --qf '%{VENDOR}' "$p" 2>/dev/null | tr -d '\t\r\n')
      # stderr: this runs inside $(manager_of), whose stdout is the manager field. A vendor is no origin host.
      [ -n "$v" ] && printf 'INFO        %s: rpm vendor "%s", origin repository unreadable (judged upstream)\n' "$p" "$v" >&2
    fi
  fi
  bad=$(printf '%s' "$bad" | tr -cd 'a-z0-9.-')
  if [ "$any" -eq 1 ] && [ -z "$bad" ]; then echo "pkg:$p$uni"; return; fi
  echo "repo:${bad:-unknown}:$p"
}
manager_of(){  # how a binary was installed
  local b="$1" o
  case "$b" in */.nvm/*) echo nvm; return;; */.volta/*) echo volta; return;; */fnm/*) echo fnm; return;;
               */conda*/*|*/miniforge*/*) echo conda; return;; */.pyenv/*) echo pyenv; return;; esac
  o=$(pkg_owner "$b"); [ -n "$o" ] && { pkg_origin "$o"; return; }
  echo tarball
}

osrel_get(){  # one os-release value, quotes stripped (the file is parsed, never sourced)
  sed -n "s/^$1=//p" "${AGENTTEAMS_L1_ROOT:-}/etc/os-release" 2>/dev/null | head -1 \
    | sed -e "s/^[\"']//" -e "s/[\"']\$//" | tr -d '\t\r'
}
esm_line(){  # Ubuntu Pro ESM state from the client's last status file; `pro status` can contact the network
  local f="${AGENTTEAMS_L1_ROOT:-}/var/lib/ubuntu-advantage/status.json" out=""   # set: bash 5 + set -u
  if [ -r "$f" ] && command -v python3 >/dev/null 2>&1; then
    out=$(python3 -I - "$f" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
except (OSError, ValueError):
    d = None
d = d if isinstance(d, dict) else {}
svc = {s.get("name"): s.get("status") for s in (d.get("services") or []) if isinstance(s, dict)}
def state(name):
    if d.get("attached") is False:
        return "disabled"
    v = svc.get(name)
    return "enabled" if v == "enabled" else "disabled" if v in ("disabled", "n/a") else "unknown"
print("ESM\t%s\t%s" % (state("esm-infra"), state("esm-apps")))
PY
)
  fi
  case "$out" in ESM*) ;; *) out=$(printf 'ESM\tunknown\tunknown');; esac
  printf '%s\n' "$out"
  info "Ubuntu Pro ESM (esm-infra, esm-apps): $(printf '%s' "$out" | cut -f2-3 | tr '\t' ' ') — from status.json; L1 never runs pro"
}

os_section(){
  sec "OS"
  local ID VERSION_ID PRETTY_NAME
  ID=$(osrel_get ID); VERSION_ID=$(osrel_get VERSION_ID); PRETTY_NAME=$(osrel_get PRETTY_NAME)
  echo "host: $(hostname)  os: ${PRETTY_NAME:-unknown}  kernel: $(uname -r)  arch: $(uname -m)  at: $(date -u +%FT%TZ)"
  printf 'OSREL\t%s\t%s\t%s\n' "${ID:-unknown}" "$VERSION_ID" "${PRETTY_NAME:-${ID:-unknown}}"
  [ "$ID" = ubuntu ] && esm_line
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

rt(){ printf 'RUNTIME\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(manager_of "$3")"; }
# Binaries of these names on PATH, in /usr/sbin, /usr/local/{bin,sbin} and any extra paths given after "--",
# one "<resolved path>\t<path to run>" line per resolved file. The probe runs the unresolved path: a binary such
# as redis-server picks its behaviour from argv[0], so running the resolved target could select another mode.
bins_of(){
  local n p names=() extra=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do names+=("$1"); shift; done
  [ "${1:-}" = -- ] && shift; extra=("$@")
  { for n in "${names[@]}"; do command -v "$n" 2>/dev/null; ls -d /usr/sbin/"$n" /usr/local/bin/"$n" /usr/local/sbin/"$n" 2>/dev/null; done
    [ ${#extra[@]} -gt 0 ] && ls -d "${extra[@]}" 2>/dev/null; } \
    | while IFS= read -r p; do [ -f "$p" ] && [ -x "$p" ] && printf '%s\t%s\n' "$(readlink -f "$p" 2>/dev/null || echo "$p")" "$p"; done \
    | awk -F'\t' '!seen[$1]++'
}
unreadable(){ info "$1 at $2: version unreadable (not judged)"; }

server_section(){
  sec "Server software and other runtimes (version probes only)"
  local real run out v k pg
  while IFS=$'\t' read -r real run; do
    v=$(tmo "$run" -v 2>&1 | sed -nE 's#.*nginx/([0-9]+\.[0-9]+\.[0-9]+).*#\1#p' | head -1)
    if [ -n "$v" ]; then rt nginx "$v" "$real"; else unreadable nginx "$real"; fi
  done < <(bins_of nginx)
  while IFS=$'\t' read -r real run; do
    out=$(tmo "$run" version 2>/dev/null | head -1)
    case "$out" in
      "OpenSSL "*) v=$(printf '%s\n' "$out" | sed -nE 's/^OpenSSL ([0-9]+\.[0-9]+\.[0-9]+[a-z]*).*/\1/p');;
      "LibreSSL "*) v=$(printf '%s\n' "$out" | sed -nE 's/^LibreSSL ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p')
                    [ -n "$v" ] && { rt libressl "$v" "$real"; continue; };;   # reported as not judged
      *) v="";;
    esac
    if [ -n "$v" ]; then rt openssl "$v" "$real"; else unreadable openssl "$real"; fi
  done < <(bins_of openssl)
  # The server binary first; psql (the client, whose version may differ) only when no server binary is found.
  pg=$(bins_of postgres -- /usr/lib/postgresql/*/bin/postgres /usr/pgsql-*/bin/postgres)
  [ -n "$pg" ] || pg=$(bins_of psql)
  while IFS=$'\t' read -r real run; do
    [ -n "$run" ] || continue
    v=$(tmo "$run" --version 2>/dev/null | sed -nE 's/.*\(PostgreSQL\) ([0-9]+\.[0-9]+(\.[0-9]+)?).*/\1/p' | head -1)
    if [ -n "$v" ]; then rt postgresql "$v" "$real"; else unreadable postgresql "$real"; fi
  done <<< "$pg"
  while IFS=$'\t' read -r real run; do
    out=$(tmo "$run" --version 2>/dev/null | head -1)
    case "$out" in *MariaDB*) k=mariadb;; *) k=mysql;; esac
    v=$(printf '%s\n' "$out" | sed -nE 's/.* Ver ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p')
    if [ -n "$v" ]; then rt "$k" "$v" "$real"; else unreadable "mysqld/mariadbd" "$real"; fi
  done < <(bins_of mysqld mariadbd)
  while IFS=$'\t' read -r real run; do
    v=$(tmo "$run" --version 2>/dev/null | sed -nE 's/.* v=([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -1)
    if [ -n "$v" ]; then rt redis "$v" "$real"; else unreadable redis "$real"; fi
  done < <(bins_of redis-server)
  while IFS=$'\t' read -r real run; do
    v=$(RUBYOPT= tmo "$run" -v 2>/dev/null | sed -nE 's/^ruby ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -1)
    if [ -n "$v" ]; then rt ruby "$v" "$real"; else unreadable ruby "$real"; fi
  done < <(bins_of ruby)
  while IFS=$'\t' read -r real run; do   # GOTOOLCHAIN=local, from /: `go` never fetches a toolchain
    v=$(cd / && GOTOOLCHAIN=local GOFLAGS= tmo "$run" version 2>/dev/null | sed -nE 's/.* go([0-9]+\.[0-9]+(\.[0-9]+)?).*/\1/p' | head -1)
    if [ -n "$v" ]; then rt go "$v" "$real"; else unreadable go "$real"; fi
  done < <(bins_of go -- /usr/local/go/bin/go /usr/lib/go-*/bin/go)
  while IFS=$'\t' read -r real run; do
    out=$(JAVA_TOOL_OPTIONS= JDK_JAVA_OPTIONS= _JAVA_OPTIONS= tmo "$run" -version 2>&1)
    v=$(printf '%s\n' "$out" | sed -nE 's/.*version "([0-9][0-9._]*)".*/\1/p' | head -1)
    case "$out" in *Corretto*) k=java-corretto;; *) k=java;; esac
    if [ -n "$v" ]; then rt "$k" "$v" "$real"; else unreadable java "$real"; fi
  done < <(bins_of java -- /usr/lib/jvm/*/bin/java /usr/lib/jvm/*/jre/bin/java)
  while IFS=$'\t' read -r real run; do
    out=$(DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 tmo "$run" --list-runtimes 2>/dev/null \
          | sed -nE 's/^Microsoft\.[A-Za-z.]+ ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | sort -u)
    [ -n "$out" ] || { unreadable dotnet "$real"; continue; }
    for v in $out; do rt dotnet "$v" "$real"; done
  done < <(bins_of dotnet -- /usr/share/dotnet/dotnet /usr/lib/dotnet/dotnet /opt/dotnet/dotnet)
  while IFS=$'\t' read -r real run; do   # the local socket only; no socket access is information, not a failure
    v=$(DOCKER_HOST=unix:///var/run/docker.sock tmo "$run" version --format '{{.Server.Version}}' 2>/dev/null \
        | sed -nE 's/^([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -1)
    if [ -n "$v" ]; then rt docker "$v" "$real"; else info "docker present, server version unreadable (no socket access)"; fi
  done < <(bins_of docker)
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
    ex="${ex#argv[]=}"; ex="${ex%% *}"   # the executable only: arguments can carry inline secrets
    echo "$ex" | grep -qiE 'python|gunicorn|uwsgi|uvicorn|node|npm|pm2|php|java|docker|ruby' && info "$u → $ex"
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
  if $DEEP; then
    for e in ${_IE[@]+"${_IE[@]}"}; do grep -qsl -- "$e" /proc/[0-9]*/environ 2>/dev/null && { ioc "a process has $e in its environment"; any=1; }; done
  elif [ ${#_IE[@]} -gt 0 ]; then
    info "${#_IE[@]} env indicator rule(s) not checked: process environments hold secrets (run L1 with --deep to check them)"
  fi
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
  os_section; runtime_section; server_section; packages_section; services_section; ioc_section; docroot_section
  sec "RESULT"
  case "$RC" in
    0) echo "RESULT clean (package/runtime verdicts come from the workstation report)";;
    1) echo "RESULT action-needed";;
    4) echo "RESULT COMPROMISE — isolate the host, stop the payload, rebuild, rotate every credential it could reach";;
  esac
  return "$RC"
}

DEEP=false
for _arg in "$@"; do
  case "$_arg" in
    --deep) DEEP=true;;   # also read process environments for `env` indicator rules
    *) echo "unknown option: $_arg (L1 takes only --deep)" >&2; exit 2;;
  esac
done
main < /dev/null
