#!/usr/bin/env bash
# L1 — READ-ONLY vulnerability inventory of one Linux host. Changes nothing it manages.
# Run as root, the package tools it queries may still touch their own state: `apt-cache policy` runs with its
# binary caches off (Dir::Cache::pkgcache= and srcpkgcache=), so it writes no cache file; `dnf -C repoquery` and
# `dnf -C updateinfo` read the cache only (-C) but may append a line to dnf's own log (/var/log/dnf*.log). `nginx -T` (run once, when
# nginx exists) may create the error-log file and temp directories its configuration names, as root, if they are
# missing; its output goes to one mktemp file that an EXIT trap removes. Nothing else is written.
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
#   DISTROPKG  location  name  version  binary  source  pkgversion  origin
#                                                     a package in an OS-managed Python path (/usr/lib*/python3*): the
#                                                     dpkg/rpm package owning it, its source package, that package's
#                                                     installed version and its origin (pkg:/repo: as for RUNTIME);
#                                                     "-" and origin "unowned" when no package owns it (plan vfr-a A1)
#   AUTOUPD    manager  key  value                    automatic-update settings: unattended-upgrades' allowed origins
#                                                     and APT::Periodic values, or dnf-automatic's [commands] values and
#                                                     enabled timers; and the package lists' / metadata's age in hours
#   SCOPE      path  label  match  evidence           a location on disk that is not the live system (plan vfr-e):
#                                                     label "disabled" (match "tree": the path and everything under it)
#                                                     or "on-disk-not-served" (match "exact": one npm manifest location).
#                                                     A label is information only: every finding keeps its row and grade
#   PYSITE     location  X.Y                          the interpreter line a site-packages directory belongs to (vfr-r R4)
#   UPDATEINFO pass  advisory  CVE  NEVRA              dnf only: one CVE of a cached security advisory (`dnf -C updateinfo
#                                                     list --security --installed|--available --with-cve`); pass is
#                                                     "installed" or "available", advisory "-" (the listing names CVEs)
#   UPDATEINFOREAD  pass  ok|failed                   whether that pass was read: dnf failed, or a line was not
#                                                     "<CVE> <severity>/Sec. <NEVRA>", is "failed" (no records printed)
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
# Secret-bearing surfaces are never printed, and by default never read: no process environment (/proc/*/environ),
# no `systemctl cat`, no journal; a running service is shown by its executable path only (never its arguments,
# which can carry inline secrets). `env` indicator rules need process environments, so they run only with --deep
# (an operator decision per host). The one exception to "never read" is the disabled-tree check below.
#
# AGENTTEAMS_L1_ROOT (tests only) prefixes the files read for OSREL, ESM and dnf-automatic, the configuration and
# /proc entries (and the docker socket test) the disabled-tree check reads, and the dnf metadata cache, and is stripped from a site-packages path
# before the OS-managed test; unset on a real host.
#
# The disabled-tree check (E1) runs only when a <name>.disabled-YYYYMMDD tree exists. It reads unit files (and the
# EnvironmentFile= files they name), cron and at spools, profile, PAM, /etc/default, logrotate, PHP, loader and
# web-server configuration, pm2 dumps, process mountinfo, container mount sources and every user's dotfiles only
# for a fixed-string match on the tree's name (grep -l), and prints at most the path of the file that matched,
# never its content. Dotfiles:
#   *_history *-history .lesshst .viminfo .pgpass .netrc .git-credentials .my.cnf   never read: they load nothing
#   .env .npmrc .pypirc .vault-token .s3cfg .boto                                   secret stores that can also
#                                                                                   point at code: only grep -lF for
#                                                                                   the tree's name, as for
#                                                                                   EnvironmentFile= files (path
#                                                                                   only, stderr discarded)
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
# Python package locations the OS package manager owns (engine.DISTRO_LOCATION), and the interpreter line in a path.
DISTRO_SP_RE='^/usr/lib(64)?/python3[^/]*/(dist|site)-packages/?$'
PYSITE_RE='/python([0-9]+\.[0-9]+)/(site|dist)-packages/?$'
# A renamed tree's suffix: <name>.disabled-YYYYMMDD (E1).
DISABLED_RE='\.disabled-(20[0-9][0-9])(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$'
DISABLED_TREES=""   # "<tree>\t<YYYY-MM-DD>" lines: the disabled trees labelled inert by disabled_section
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

distro_pkg(){  # A1: one DISTROPKG line for package dir $2 (PyPI name $3, version $4) in OS-managed path $1
  local o="" src="" pv="" alt="" org=unowned
  o=$(pkg_owner "$2"); o="${o%%,*}"; o="${o%% *}"
  if [ -n "$o" ]; then
    if command -v dpkg >/dev/null 2>&1; then
      IFS=$'\t' read -r src pv alt < <(dpkg-query -W -f='${source:Package}\t${Version}\t${Source}\n' "$o" 2>/dev/null | head -1)
      [ -n "$src" ] || src="${alt%% *}"; [ -n "$src" ] || src="$o"   # an empty Source field means "same as binary"
    else
      IFS=$'\t' read -r src pv < <(rpm -q --qf '%{SOURCERPM}\t%{VERSION}-%{RELEASE}\n' "$o" 2>/dev/null | head -1)
      src="${src%.src.rpm}"; src="${src%-*}"; src="${src%-*}"   # name-version-release.src.rpm -> name
    fi
    org=$(pkg_origin "$o")
  fi
  printf 'DISTROPKG\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$3" "$4" "${o:--}" "${src:--}" "${pv:--}" "$org" | tr -d '\r'
}
apt_autoupd(){  # A1: what unattended-upgrades may install ($1 = package-list age in hours, or "unknown")
  local out=""
  if command -v apt-config >/dev/null 2>&1; then
    out=$(tmo apt-config dump 2>/dev/null | awk '
      $1 ~ /^(Unattended-Upgrade::(Allowed-Origins|Origins-Pattern|Package-Blacklist|Automatic-Reboot)|APT::Periodic::(Enable|Update-Package-Lists|Unattended-Upgrade))(::)?$/ {
        k = $1; sub(/::$/, "", k); v = $0; sub(/^[^ ]+ /, "", v); sub(/;[ \t\r]*$/, "", v); gsub(/^"|"$/, "", v)
        gsub(/\t/, " ", v); if (v != "") printf "AUTOUPD\tapt\t%s\t%s\n", k, v }')
    if [ -n "$out" ]; then printf '%s\n' "$out"; else printf 'AUTOUPD\tapt\tconfig\tnothing set\n'; fi
  else printf 'AUTOUPD\tapt\tconfig\tunreadable (no apt-config)\n'; fi
  printf 'AUTOUPD\tapt\tlists-age-hours\t%s\n' "$1"
}
dnf_autoupd(){  # A1: dnf-automatic's [commands] settings (a key not printed is unset: dnf's default), timers, cache age
  local f="${AGENTTEAMS_L1_ROOT:-}/etc/dnf/automatic.conf" t m c newest=0
  if [ -r "$f" ]; then
    awk '/^[ \t]*\[/{s = $0; gsub(/[ \t\r]/, "", s); next}
         s == "[commands]" && /^[ \t]*(apply_updates|upgrade_type|download_updates)[ \t]*=/ {
           k = $0; sub(/[ \t]*=.*/, "", k); gsub(/[ \t]/, "", k); v = $0; sub(/^[^=]*=[ \t]*/, "", v)
           sub(/[ \t\r]+$/, "", v); gsub(/\t/, " ", v); printf "AUTOUPD\tdnf\t%s\t%s\n", k, v }' "$f"
  else printf 'AUTOUPD\tdnf\tconfig\tunreadable (no /etc/dnf/automatic.conf)\n'; fi
  for t in dnf-automatic.timer dnf-automatic-install.timer dnf-automatic-download.timer dnf-automatic-notifyonly.timer; do
    systemctl is-enabled "$t" >/dev/null 2>&1 && printf 'AUTOUPD\tdnf\ttimer\t%s enabled\n' "$t"
  done
  for m in "${AGENTTEAMS_L1_ROOT:-}"/var/cache/dnf/*/repodata/repomd.xml; do
    [ -f "$m" ] || continue
    c=$(stat -c %Y "$m" 2>/dev/null || stat -f %m "$m" 2>/dev/null || echo 0)
    [ "$c" -gt "$newest" ] && newest=$c
  done
  if [ "$newest" -gt 0 ]; then t=$(( ( $(date +%s) - newest ) / 3600 )); else t=unknown; fi
  printf 'AUTOUPD\tdnf\tmetadata-age-hours\t%s\n' "$t"
  info "dnf metadata age (hours, newest cached repomd.xml): $t"
}

dnf_updateinfo(){  # A2: per-CVE security advisories from the cached updateinfo, for the AL2023 distro verdicts
  # Cache only (-C), under tmo. The --with-cve listing's format is unverified, so it is read strictly: a pass dnf
  # fails, or with any line that is not "[i] <CVE> <severity>/Sec. <NEVRA>", prints no records and is "failed"
  # (the workstation then reports a feed error, never "no advisory").
  local pass out="" recs="" rc=0
  command -v dnf >/dev/null 2>&1 || return 0
  for pass in installed available; do
    rc=0; recs=""
    out=$(tmo dnf -C -q updateinfo list --security "--$pass" --with-cve 2>/dev/null) || rc=$?
    if [ "$rc" -eq 0 ]; then
      recs=$(printf '%s\n' "$out" | awk -v p="$pass" '
        { sub(/\r$/, ""); gsub(/^[ \t]+|[ \t]+$/, "") }
        $0 == "" { next }
        { n = split($0, t, /[ \t]+/); i = 1
          if (t[1] == "i" && n == 4) i = 2                       # an "installed" marker column
          if (n - i + 1 != 3 || t[i] !~ /^CVE-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]+$/ || t[i+1] !~ /\/Sec\.$/ \
              || t[i+2] !~ /^[A-Za-z0-9][A-Za-z0-9._+:~^-]*-[^-\/]+-[^-\/]+$/) { bad = 1; exit }
          printf "UPDATEINFO\t%s\t-\t%s\t%s\n", p, t[i], t[i+2] }
        END { if (bad) exit 3 }') || rc=$?
    fi
    if [ "$rc" -eq 0 ]; then
      [ -n "$recs" ] && printf '%s\n' "$recs"
      printf 'UPDATEINFOREAD\t%s\tok\n' "$pass"
    else
      printf 'UPDATEINFOREAD\t%s\tfailed\n' "$pass"
      info "dnf updateinfo --$pass --with-cve unreadable or unparseable: AL2023 distro verdicts unknown"
    fi
  done
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
    [ "$stamp" -gt 0 ] || age=unknown
    apt_autoupd "$age"
  elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    local pm n; pm=$(command -v dnf || command -v yum)
    n=$($pm -q --cacheonly updateinfo list --security 2>/dev/null | grep -c .)
    [ "$n" -gt 0 ] && check HIGH "$n pending OS security advisories (L2-os-patch.sh)" || ok "no pending security advisories (per cached metadata)"
    dnf_updateinfo
    if command -v needs-restarting >/dev/null 2>&1; then needs-restarting -r >/dev/null 2>&1 || check MODERATE "reboot required (needs-restarting -r)"; fi
    if systemctl is-enabled dnf-automatic.timer >/dev/null 2>&1 || systemctl is-enabled dnf-automatic-install.timer >/dev/null 2>&1; then
      ok "dnf-automatic timer enabled"
    else check MODERATE "no automatic security patching (dnf-automatic not enabled)"; fi
    dnf_autoupd
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

# E1: what outside tree $1 still names it, or why that could not be read; nothing printed = inert. The check fails
# closed (Rule 12): any reference, or a source it cannot read, leaves the tree unlabelled, so its findings read as live.
tree_ref(){
  local t="$1" R="${AGENTTEAMS_L1_ROOT:-}" base f h out rc u e c opt
  local srcs=() envf=() roots=()
  base=$(basename "$t")
  # configuration that starts or loads code: units (system, user, transient, generated), cron and at, profiles,
  # PAM, defaults, logrotate, PHP, loader (ld.so.preload too), web servers and process supervisors
  for f in "$R"/etc/systemd "$R"/run/systemd/system "$R"/run/systemd/transient "$R"/run/systemd/generator* \
           "$R"/lib/systemd/system "$R"/usr/lib/systemd/system "$R"/usr/lib/systemd/user "$R"/usr/local/lib/systemd \
           "$R"/etc/crontab "$R"/etc/cron.d "$R"/etc/cron.hourly "$R"/etc/cron.daily "$R"/etc/cron.weekly \
           "$R"/etc/cron.monthly "$R"/etc/anacrontab "$R"/var/spool/cron "$R"/var/spool/at \
           "$R"/etc/profile "$R"/etc/profile.d "$R"/etc/environment "$R"/etc/bash.bashrc "$R"/etc/bashrc "$R"/etc/zsh \
           "$R"/etc/zshrc "$R"/etc/pam.d "$R"/etc/default "$R"/etc/logrotate.d "$R"/etc/php* \
           "$R"/etc/ld.so.conf "$R"/etc/ld.so.conf.d "$R"/etc/ld.so.preload "$R"/etc/nginx "$R"/etc/apache2 "$R"/etc/httpd \
           "$R"/etc/supervisor "$R"/etc/supervisord.conf "$R"/etc/supervisord.d "$R"/etc/uwsgi "$R"/etc/rc.local \
           "$R"/etc/init.d "$R"/etc/xdg/autostart; do
    [ -e "$f" ] && srcs+=("$f")
  done
  # every user's dotfiles, user-level units and pm2 dump. Never read: history and credential-only files (they load
  # nothing: skipped); secret stores that can also point at code (present: unverified, so no label)
  for h in "$R"/root "$R"/home/*; do
    [ -d "$h" ] || continue
    for f in "$h"/.[!.]*; do
      [ -f "$f" ] || continue
      case "$(basename "$f")" in
        *_history|*-history|.lesshst|.viminfo|.pgpass|.netrc|.git-credentials|.my.cnf) continue;;
        .env|.npmrc|.pypirc|.vault-token|.s3cfg|.boto) envf+=("$f"); continue;;   # grep -lF only, never -h
      esac
      srcs+=("$f")
    done
    for f in "$h"/.config/systemd "$h"/.config/environment.d "$h"/.config/autostart "$h"/.pm2/dump.pm2; do
      [ -e "$f" ] && srcs+=("$f")
    done
  done
  # the files units load their environment from: matched with grep -lF, never printed. One leading "-" makes the file
  # optional: missing, it loads nothing (systemd ignores it). Anything else this check cannot resolve to one regular
  # file (quoted, a %-specifier, several paths, relative, not a regular file, missing without "-") is unverified.
  if [ ${#srcs[@]} -gt 0 ]; then
    while IFS= read -r f; do
      f="${f#*EnvironmentFile=}"; f="${f%"${f##*[![:space:]]}"}"; opt=0
      case "$f" in -*) opt=1; f="${f#-}";; esac
      case "$f" in ""|*\"*|*\'*|*%*|*[[:space:]]*) echo "unverified: EnvironmentFile=$f is not resolved"; return;;
                   /*) ;; *) echo "unverified: EnvironmentFile=$f is not an absolute path"; return;; esac
      if [ -e "$R$f" ] || [ -L "$R$f" ]; then
        [ -f "$R$f" ] || { echo "unverified: EnvironmentFile=$f is not a regular file"; return; }
        envf+=("$R$f")
      elif [ "$opt" -eq 0 ]; then
        echo "unverified: EnvironmentFile=$f does not exist"; return
      fi
    done < <(grep -rhE '^[[:space:]]*EnvironmentFile=' "${srcs[@]}" 2>/dev/null | sort -u)
  fi
  if [ ${#srcs[@]} -gt 0 ] || [ ${#envf[@]} -gt 0 ]; then
    out=$(grep -rlF -- "$base" ${srcs[@]+"${srcs[@]}"} ${envf[@]+"${envf[@]}"} 2>/dev/null); rc=$?
    out=$(printf '%s\n' "$out" | grep -vF -- "$t/" | head -1)
    [ -n "$out" ] && { echo "referenced by $out"; return; }
    [ "$rc" -eq 2 ] && { echo "unverified: a configuration file could not be read"; return; }
  fi
  if command -v nginx >/dev/null 2>&1; then   # -T: the whole configuration, includes outside /etc/nginx too
    [ "$NGINX_RC" -eq 0 ] || { echo "unverified: nginx -T failed"; return; }
    grep -qF -- "$base" "$NGINX_DUMP" && { echo "referenced by the nginx configuration (nginx -T)"; return; }
  fi
  # a container that mounts it (bind mounts outside containers show in mountinfo, below); unreadable: unverified
  for c in docker podman; do
    command -v "$c" >/dev/null 2>&1 || continue
    # no daemon socket and no daemon binary: a docker client alone runs no container (podman needs no daemon)
    [ "$c" = docker ] && [ ! -e "$R"/var/run/docker.sock ] && ! command -v dockerd >/dev/null 2>&1 && continue
    out=$(DOCKER_HOST=unix:///var/run/docker.sock tmo "$c" ps -aq 2>/dev/null) || { echo "unverified: $c ps failed"; return; }
    [ -n "$out" ] || continue
    # shellcheck disable=SC2086   # container ids: one word each
    out=$(DOCKER_HOST=unix:///var/run/docker.sock tmo "$c" inspect --format '{{range .Mounts}}{{.Source}} {{end}}' $out \
          2>/dev/null) || { echo "unverified: $c inspect failed"; return; }
    case "$out" in *"$base"*) echo "mounted into a $c container"; return;; esac
  done
  # .pth / .egg-link / site- and usercustomize files outside the tree that name it (a find error: unverified)
  for f in $SCAN_ROOTS; do [ -d "$f" ] && [ ! -L "$f" ] && roots+=("$f"); done
  if [ ${#roots[@]} -gt 0 ]; then
    out=$(find "${roots[@]}" -xdev -maxdepth 9 -type f \( -name '*.pth' -o -name '*.egg-link' -o -name sitecustomize.py \
          -o -name usercustomize.py \) -print 2>/dev/null) || { echo "unverified: the .pth search failed"; return; }
    while IFS= read -r f; do
      [ -n "$f" ] && [ -f "$f" ] || continue
      case "$f" in "$t"/*) continue;; esac
      grep -qF -- "$base" "$f" 2>/dev/null && { echo "referenced by $f"; return; }
    done <<< "$out"
  fi
  # symlinks outside the tree into it (a link reaches it only by naming it, or through another such link)
  for f in /usr/bin /usr/sbin /bin /sbin /etc; do [ -d "$f" ] && [ ! -L "$f" ] && roots+=("$f"); done
  if [ ${#roots[@]} -gt 0 ]; then
    out=$(find "${roots[@]}" -xdev -maxdepth 8 -type l -lname "*$base*" -print 2>/dev/null) \
      || { echo "unverified: the symlink search failed"; return; }
    while IFS= read -r f; do
      [ -n "$f" ] && [ -L "$f" ] || continue
      case "$f" in "$t"/*) continue;; esac
      echo "symlink $f points into it"; return
    done <<< "$out"
  fi
  # a running process whose binary or working directory is in it, that has one of its files mapped, or whose mount
  # table (bind mounts, container namespaces) names it
  for u in "$R"/proc/[0-9]*; do
    [ -d "$u" ] || continue
    e=$(readlink "$u/exe" 2>/dev/null); c=$(readlink "$u/cwd" 2>/dev/null)
    case "$e|$c" in *"$base"*) echo "running process $(basename "$u") runs from it"; return;; esac
    grep -qF -- "$base" "$u/maps" 2>/dev/null && { echo "running process $(basename "$u") has its files loaded"; return; }
    grep -qF -- "$base" "$u/mountinfo" 2>/dev/null && { echo "mounted (process $(basename "$u")'s mountinfo)"; return; }
  done
}

disabled_section(){
  sec "Disabled trees (renamed <name>.disabled-YYYYMMDD; their findings keep their rows and grades)"
  local t base ref since any=0
  while IFS= read -r t; do
    [ -n "$t" ] && [ -d "$t" ] && [ ! -L "$t" ] || continue
    base=$(basename "$t")
    [[ "$base" =~ $DISABLED_RE ]] || continue
    since="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"; any=1
    ref=$(tree_ref "$t")
    if [ -z "$ref" ]; then
      printf 'SCOPE\t%s\tdisabled\ttree\tinert since %s\n' "$t" "$since"
      DISABLED_TREES="$DISABLED_TREES$t"$'\t'"$since"$'\n'
      info "disabled tree $t: inert since $since (no unit, cron, profile, dotfile, loader, web-server, .pth, symlink or process reference)"
    else
      info "disabled tree $t: not labelled, $ref (its findings read as live)"
    fi
  done < <(find $SCAN_ROOTS -xdev -maxdepth 4 -type d -name '*.disabled-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' -prune -print 2>/dev/null | sort -u)
  [ "$any" -eq 0 ] && ok "no disabled trees"
}
scope_note(){  # " (in disabled tree <t>, inert since <d>)" when path $1 lies in a tree disabled_section labelled
  local t d
  while IFS=$'\t' read -r t d; do
    [ -n "$t" ] || continue
    case "$1" in "$t"|"$t"/*) printf ' (in disabled tree %s, inert since %s)' "$t" "$d"; return;; esac
  done <<< "$DISABLED_TREES"
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
  local sp d f n line distro xy _t _e _n _v _l
  for sp in $(find $SCAN_ROOTS -xdev -maxdepth 8 -path '*/node_modules' -prune -o -type d \( -name site-packages -o -name dist-packages \) -print 2>/dev/null | sort -u); do
    n=0; distro=0; [[ "${sp#"${AGENTTEAMS_L1_ROOT:-}"}" =~ $DISTRO_SP_RE ]] && distro=1
    for d in "$sp"/*.dist-info "$sp"/*.egg-info; do
      [ -e "$d" ] || continue
      f="$d/METADATA"; [ -f "$f" ] || f="$d/PKG-INFO"; [ -f "$f" ] || continue
      line=$(awk -v loc="$sp" 'BEGIN{FS=": "} /^Name: /{n=$2} /^Version: /{v=$2} /^$/{exit} END{if(n!=""&&v!="") printf "INV\tPyPI\t%s\t%s\t%s\n", n, v, loc}' "$f" | tr -d '\r')
      [ -n "$line" ] && printf '%s\n' "$line"
      if [ "$distro" -eq 1 ] && [ -n "$line" ]; then   # A1: which OS package owns it, and where that came from
        IFS=$'\t' read -r _t _e _n _v _l <<< "$line"
        distro_pkg "$sp" "$d" "$_n" "$_v"
      fi
      n=$((n+1))
    done
    [ "$n" -gt 0 ] || continue
    info "$sp: $n packages$(scope_note "$sp")"
    xy=""   # R4: the interpreter line, from the path or (Debian's python3/dist-packages) the system python3
    if [[ "$sp" =~ $PYSITE_RE ]]; then xy="${BASH_REMATCH[1]}"
    elif [ "$sp" = /usr/lib/python3/dist-packages ] && [ -x /usr/bin/python3 ]; then
      xy=$(tmo /usr/bin/python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    fi
    [[ "$xy" =~ ^[0-9]+\.[0-9]+$ ]] && printf 'PYSITE\t%s\t%s\n' "$sp" "$xy"
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
  served_labels "$manifests"
}

# E2b: an npm manifest location whose node_modules sits under a web root but which every nginx server covering it
# refuses (deny all / internal / return 403|404) gets SCOPE "on-disk-not-served", with the rule as evidence. Fails
# closed: no label when nginx -T fails, Apache is present (its rules are not read), a location sits outside every
# server (an included snippet), a server holds an include, a root or alias is relative, an alias or a location-level
# root reaches the tree, any probe URI selects a location that serves, or the server has a regex location and the
# refusal is not a ^~ or = location (regex order and captures are not judged).
# The parser stays inline: remote.sh streams this one script over ssh stdin (plus the IOC data heredoc), so a second
# kit file would need its own embedding channel, and bash 3.2 (the compatibility floor) misparses a $(cat <<'EOF')
# capture of this code's quotes.
served_labels(){
  local locs
  command -v nginx >/dev/null 2>&1 || return 0
  locs=$(printf '%s\n' "$1" | sed -n 's#/node_modules/\.package-lock\.json$##p')
  [ -n "$locs" ] || return 0
  if command -v apache2ctl >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1; then
    info "Apache present: no node_modules location is labelled not-served (Apache rules are not read)"; return 0
  fi
  [ "$NGINX_RC" -eq 0 ] || { info "nginx -T failed: no node_modules location is labelled not-served"; return 0; }
  python3 -I - "$locs" "$NGINX_DUMP" <<'PY'
import os, re, sys

def tokens(text):
    for t in re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|#[^\n]*|[{};]|[^\s{};"\']+', text):
        if t.startswith("#"):
            continue
        yield t[1:-1] if t[:1] in "\"'" else t

def parse(text):
    top, stack, cur = [], [], []
    stack.append(top)
    for t in tokens(text):
        if t == ";":
            stack[-1].append((cur, None)); cur = []
        elif t == "{":
            blk = []; stack[-1].append((cur, blk)); stack.append(blk); cur = []
        elif t == "}":
            if len(stack) == 1:
                raise ValueError("unbalanced")
            stack.pop(); cur = []
        else:
            cur.append(t)
    if len(stack) != 1:
        raise ValueError("unbalanced")
    return top

def under(path, root):
    root = os.path.realpath(root).rstrip("/") or "/"
    return path == root or path.startswith(root + "/")

def walk(block, want):
    for args, sub in block:
        if args and args[0] in want:
            yield args, sub
        if sub is not None:
            yield from walk(sub, want)

def location(args):
    a = args[1:]
    if len(a) == 2 and a[0] in ("=", "^~", "~", "~*"):
        return a[0], a[1]
    if len(a) == 1 and not a[0].startswith("~"):
        for m in ("^~", "="):   # these two may be written glued to the path
            if a[0].startswith(m) and len(a[0]) > len(m):
                return m, a[0][len(m):]
        return "", a[0]
    return None, None

def select(locs, uri):
    best = None
    for mod, arg, sub in locs:
        if mod == "=" and arg == uri:
            return mod, arg, sub
        if mod in ("", "^~") and uri.startswith(arg) and (best is None or len(arg) > len(best[1])):
            best = (mod, arg, sub)
    if best and best[0] == "^~":
        return best
    for mod, arg, sub in locs:   # regex locations in file order; re.error is caught by the caller (not served: no)
        if mod in ("~", "~*") and re.search(arg, uri, re.I if mod == "~*" else 0):
            return mod, arg, sub
    return best

def refuses(sel):
    if sel is None:
        return ""
    mod, arg, sub = sel
    if any(a and a[0] in ("location", "allow", "if", "rewrite") for a, _s in sub):
        return ""   # nested locations, allow rules, conditionals or rewrites: not judged
    for a, s in sub:
        if s is None and (a == ["deny", "all"] or a == ["internal"]
                          or (a[:1] == ["return"] and a[1:2] in (["403"], ["404"]))):
            return f"location {mod + ' ' if mod else ''}{arg} {{ {' '.join(a)}; }}"
    return ""

def judge(conf, loc):
    """(evidence, "") when every server whose root holds loc/node_modules refuses it, else ("", why)."""
    nm = os.path.realpath(loc + "/node_modules")
    top = list(conf) + [x for a, sub in conf if a[:1] == ["http"] and sub is not None for x in sub]
    if any(a[:1] == ["root"] and s is None for a, s in top):
        return "", "an http-level root"
    if any(a[:1] == ["location"] for a, _s in top):   # an included snippet: nginx -T cannot say which server has it
        return "", "a location outside any server block (an included snippet)"
    ev = []
    # nginx -T prints each included file on its own, so server blocks can sit at the top level too
    for _a, srv in walk(conf, ("server",)):
        if srv is None:
            continue   # an upstream's "server host:port;"
        if any(d[:1] == ["rewrite"] and s is None for d, s in srv):
            return "", "a server-level rewrite"
        if any(True for _d in walk(srv, ("include",))):   # its locations sit elsewhere in the dump
            return "", "an include in a server block"
        for d, _s in walk(srv, ("root", "alias")):
            if len(d) < 2 or not d[1].startswith("/"):
                return "", f"a relative or variable {d[0]}"
        for d, _s in walk([x for x in srv if x[1] is not None], ("root", "alias")):
            if under(nm, d[1]) or under(os.path.realpath(d[1]), nm):
                return "", f"a location-level {d[0]} {d[1]}"
        root = next((d[1] for d, s in srv if d[:1] == ["root"] and s is None and under(nm, d[1])), None)
        if root is None:
            continue
        locs = []
        for d, sub in srv:
            if d[:1] == ["location"] and sub is not None:
                mod, arg = location(d)
                if mod is None:
                    return "", "a location this check does not read: " + " ".join(d[1:])
                if not arg.startswith("@"):
                    locs.append((mod, arg, sub))
        regex = any(mod in ("~", "~*") for mod, _a, _s in locs)
        base = "/" + os.path.relpath(nm, os.path.realpath(root))
        rules = set()
        for uri in (base + "/", base + "/x/package.json", base + "/x/index.js", base + "/x/README.md",
                    base + "/.package-lock.json"):
            try:
                sel = select(locs, uri)
                r = refuses(sel)
            except re.error:
                r = ""
            if not r:
                return "", f"{uri} is served"
            if regex and sel[0] not in ("^~", "="):   # regex order and captures: only ^~ / = refusals are trusted
                return "", "a regex location exists; a ^~ or = refusal is needed"
            rules.add(r)
        names = next((" ".join(d[1:]) for d, s in srv if d[:1] == ["server_name"] and s is None), "_")
        ev.append(f"server {names}: " + " / ".join(sorted(rules)))
    return ("nginx " + "; ".join(ev), "") if ev else ("", "under no nginx root")

def main():
    try:
        with open(sys.argv[2], encoding="utf-8", errors="replace") as fh:
            conf = parse(fh.read())
    except (OSError, ValueError):
        print("INFO        nginx configuration unreadable: no node_modules location is labelled not-served")
        return
    for loc in [x for x in sys.argv[1].splitlines() if x]:
        evidence, why = judge(conf, loc)
        evidence = " ".join(evidence.split())
        if why:
            print(" ".join(f"INFO        {loc}/node_modules: not labelled ({why})".split("\t")))
            continue
        print(f"SCOPE\t{loc}\ton-disk-not-served\texact\t{evidence}")
        print(f"INFO        {loc}/node_modules: on disk, not served ({evidence})")

if __name__ == "__main__":   # tests import the functions above without running main
    main()
PY
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
  for g in ${_IF[@]+"${_IF[@]}"}; do for p in $g; do [ -e "$p" ] && { ioc "indicator path present: $p$(scope_note "$p")"; any=1; }; done; done
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
      for name in ${_IN[@]+"${_IN[@]}"}; do [ "$(basename "$p")" = "$name" ] && { ioc "indicator file: $p$(scope_note "$p")"; any=1; }; done
      for g in ${_IG[@]+"${_IG[@]}"}; do
        [ "$(basename "$p")" = "${g%%:*}" ] && grep -qiE -- "${g#*:}" "$p" 2>/dev/null && { ioc "indicator content in $p$(scope_note "$p")"; any=1; }
      done
    done < <(find $SCAN_ROOTS -xdev -maxdepth 9 \( "${expr[@]}" \) 2>/dev/null)
  fi
  [ "$any" -eq 0 ] && ok "no compromise indicators matched"
}

docroot_section(){
  command -v nginx >/dev/null 2>&1 || command -v apache2ctl >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1 || return 0
  sec "Secret-looking files inside web document roots"
  local r hits roots
  roots=$( { [ "$NGINX_RC" -eq 0 ] && awk '$1=="root"{gsub(";","",$2); print $2}' "$NGINX_DUMP"; \
             { tmo apache2ctl -S 2>/dev/null; tmo httpd -S 2>/dev/null; } | awk '/DocumentRoot/{gsub("\"","",$NF); print $NF}'; } | sort -u)
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

NGINX_DUMP=""; NGINX_RC=1   # the one `nginx -T` dump (nginx_dump), read by tree_ref, served_labels, docroot_section
nginx_dump(){  # run `nginx -T` once, in the main shell, into a mktemp file the EXIT trap removes; NGINX_RC 0 = usable
  command -v nginx >/dev/null 2>&1 || return 0
  NGINX_DUMP=$(mktemp "${TMPDIR:-/tmp}/l1-nginx.XXXXXX" 2>/dev/null) || { NGINX_DUMP=""; return 0; }
  trap '[ -n "$NGINX_DUMP" ] && rm "$NGINX_DUMP" 2>/dev/null' EXIT
  tmo nginx -T > "$NGINX_DUMP" 2>/dev/null; NGINX_RC=$?
}

main(){
  nginx_dump
  os_section; disabled_section; runtime_section; server_section; packages_section; services_section; ioc_section; docroot_section
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
