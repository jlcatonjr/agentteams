#!/usr/bin/env bash
# L7 — server-apply level remove-path: QUARANTINE one exact path, never delete it. Runs only under --level.
#
#   ./remote.sh <host> L7-remove-path.sh --level --path /root/anaconda3.disabled-20261101          # the plan
#   ./remote.sh <host> L7-remove-path.sh --level --path P --apply --expect-digest SHA              # quarantine it
#
# Quarantine: an archive tarball of P is written and verified first, then P is moved (never copied and deleted by
# this script) to /var/backups/agentteams/quarantine/<name>-<UTC time>/payload beside the tarball and a RESTORE
# manifest naming the original path, owner, mode, the tarball's sha256 and the restore commands. Emptying the
# quarantine is the operator's decision and is never automated.
# Refused (exit 2): a path that is not absolute and normalised, a symlink or a path with a symlinked ancestor
# (realpath must equal the path), a trust root, inside one or an ancestor of one (the list is grant_v2.TRUST_ROOTS),
# a path holding a mount point, or one that does not exist. Exit 5: the plan changed (nothing changed); 3: the
# archive or the move failed (the path is still in place, or both copies are in the quarantine directory).
set -uo pipefail
set -f

P=""; APPLY=false; LEVEL=false; EXPECT=""; TAB=$(printf '\t')
QROOT="/var/backups/agentteams/quarantine"
need(){ case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac; }
while [ $# -gt 0 ]; do case "$1" in
  --path) need "$@"; P="$2"; shift;; --expect-digest) need "$@"; EXPECT="$2"; shift;;
  --apply) APPLY=true;; --level) LEVEL=true;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

refuse(){ echo "REFUSED $P: $*"; exit 2; }
# BEGIN trust roots (identical to agentteams/vulnscan/grant_v2.py TRUST_ROOTS / TRUST_COMPONENTS)
TRUST_ROOTS="/bin /boot /dev /etc /lib /lib32 /lib64 /libx32 /proc /run /sbin /sys /usr/bin /usr/lib /usr/lib32 /usr/lib64 /usr/libexec /usr/sbin /usr/share /usr/include /var/lib /var/log /var/spool /var/backups/agentteams /var/backups/vuln-fix /root/.ssh /root/.gnupg /root/.config/agentteams"
TRUST_COMPONENTS=".ssh .gnupg authorized_keys sudoers sudoers.d"
# END trust roots
trust_problem(){   # same rule as grant_v2.trust_root_problem
  local p="$1" r c n
  [[ $p =~ ^/[A-Za-z0-9._@+/-]+$ ]] || { echo "must be an absolute path of letters, digits and ._@+-/ only"; return; }
  case "$p" in *//*|*/./*|*/../*|*/.|*/..|*/) echo "must be normalised"; return;; esac
  local parts=(); IFS=/ read -r -a parts <<< "${p#/}"; n=${#parts[@]}
  [ "$n" -ge 2 ] || { echo "a top-level directory is never quarantined"; return; }
  [ "${parts[0]}" = home ] && [ "$n" -lt 3 ] && { echo "a home directory itself is never quarantined"; return; }
  if [ "${parts[0]}" = usr ] && { [ "$n" -lt 3 ] || [ "${parts[1]}" != local ]; }; then echo "only /usr/local/<name>/... under /usr"; return; fi
  for c in "${parts[@]}"; do for r in $TRUST_COMPONENTS; do [ "$c" = "$r" ] && { echo "names ssh/gpg/sudo material"; return; }; done; done
  for r in $TRUST_ROOTS; do
    case "$p" in "$r"|"$r"/*) echo "is or lies in the trust root $r"; return;; esac
    case "$r" in "$p"/*) echo "contains the trust root $r"; return;; esac
  done
}
owner_mode(){ stat -c '%U:%G %a' -- "$1" 2>/dev/null || stat -f '%Su:%Sg %Lp' -- "$1"; }

# BEGIN plan_digest (identical in run-host.sh and every level script)
plan_digest(){
  tr -d '\r' | LC_ALL=C grep "^PLAN$TAB" | LC_ALL=C sed -E 's/[[:space:]]+/ /g; s/ $//' | LC_ALL=C sort -u \
    | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | awk '{print $1}'
}
# END plan_digest

checks(){
  local why; why=$(trust_problem "$P"); [ -z "$why" ] || refuse "$why"
  [ -e "$P" ] || [ -L "$P" ] || refuse "does not exist"
  [ ! -L "$P" ] || refuse "is a symlink"
  local real; real=$(realpath -e -- "$P" 2>/dev/null || readlink -f -- "$P" 2>/dev/null)
  [ "$real" = "$P" ] || refuse "resolves to ${real:-nothing} (a symlinked ancestor): realpath must equal the path"
  if [ -r /proc/mounts ] && awk -v p="$P" '$2 == p || index($2, p "/") == 1 {f=1} END {exit !f}' /proc/mounts; then
    refuse "holds a mount point"
  fi
}
plan(){   # one PLAN line: what is quarantined, its type, owner, mode, entry count and size
  local kind=f om n kb; [ -d "$P" ] && kind=d
  om=$(owner_mode "$P"); n=$(find "$P" -xdev 2>/dev/null | wc -l | tr -d ' '); kb=$(du -sk -- "$P" 2>/dev/null | awk '{print $1}')
  printf 'PLAN\tremove-path\t%s\tquarantine\t%s\t%s\t%s entries\t%s KiB\n' "$P" "$kind" "$om" "$n" "${kb:-0}"
}
sha(){ if command -v sha256sum >/dev/null 2>&1; then sha256sum -- "$1"; else shasum -a 256 -- "$1"; fi | awk '{print $1}'; }

main(){
  $LEVEL || refuse "L7 runs only under --level (server-apply remove-path)"
  [ -n "$P" ] || { echo "--path is required" >&2; exit 2; }
  if $APPLY && ! [[ $EXPECT =~ ^[0-9a-f]{64}$ ]]; then echo "--apply needs --expect-digest" >&2; exit 2; fi
  echo "host: $(hostname)  level: remove-path  path: $P  mode: $($APPLY && echo APPLY || echo PLAN)"
  checks
  local lplan; lplan=$(plan); printf '%s\n' "$lplan"
  if ! $APPLY; then
    printf 'PRESTATE\tpath\t%s\n' "$(ls -ld -- "$P" 2>/dev/null)"
    printf '\nPLAN ONLY — nothing changed.\n'; return 0
  fi
  [ "$(printf '%s\n' "$lplan" | plan_digest)" = "$EXPECT" ] || { echo "DIGEST MISMATCH: $P changed since the plan — nothing changed"; exit 5; }
  local ts name q; ts=$(date -u +%Y%m%dT%H%M%SZ); name=$(basename -- "$P"); q="$QROOT/$name-$ts"
  install -d -m 700 "$(dirname "$QROOT")" "$QROOT" || { echo "FAIL cannot create $QROOT"; exit 3; }
  mkdir -m 700 -- "$q" || { echo "FAIL cannot create $q"; exit 3; }
  echo "+ tar archive of $P -> $q/archive.tar.gz"
  tar --one-file-system --numeric-owner -cpzf "$q/archive.tar.gz" -C "$(dirname -- "$P")" -- "$name" \
    || { echo "FAIL archive failed — $P untouched"; exit 3; }
  tar -tzf "$q/archive.tar.gz" > /dev/null || { echo "FAIL archive does not read back — $P untouched"; exit 3; }
  local om; om=$(owner_mode "$P")
  {
    echo "# agentteams server-apply remove-path quarantine (restore manifest)"
    echo "original_path=$P"
    echo "owner_mode=$om"
    echo "quarantined_at=$ts"
    echo "payload=$q/payload"
    echo "archive=$q/archive.tar.gz"
    echo "archive_sha256=$(sha "$q/archive.tar.gz")"
    echo "plan_sha256=$EXPECT"
    echo "# restore (either): mv -T -- '$q/payload' '$P'"
    echo "#                   tar -xpzf '$q/archive.tar.gz' -C '$(dirname -- "$P")'"
    echo "# emptying the quarantine is the operator's decision; nothing in agentteams ever deletes it"
  } > "$q/RESTORE.manifest" || { echo "FAIL cannot write the restore manifest — $P untouched"; exit 3; }
  echo "+ mv -T -- $P $q/payload"
  mv -T -- "$P" "$q/payload" 2>/dev/null || mv -- "$P" "$q/payload" || { echo "FAIL move failed — see $q"; exit 3; }
  [ ! -e "$P" ] && [ -e "$q/payload" ] || { echo "FAIL $P still present after the move — see $q"; exit 3; }
  printf 'QUARANTINED\t%s\t%s\n' "$P" "$q"
}

main < /dev/null
