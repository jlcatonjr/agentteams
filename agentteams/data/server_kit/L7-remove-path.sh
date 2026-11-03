#!/usr/bin/env bash
# L7 — server-apply level remove-path: QUARANTINE one exact path, never delete it. Runs only under --level.
#
#   ./remote.sh <host> L7-remove-path.sh --level --path /root/anaconda3.disabled-20261101          # the plan
#   ./remote.sh <host> L7-remove-path.sh --level --path P --apply --expect-digest SHA              # quarantine it
#
# Quarantine: an archive tarball of P is written and verified first, then P is renamed (on the same filesystem; never
# copied and deleted) to /var/backups/agentteams/quarantine/<name>-<UTC time>/payload beside the tarball and a
# RESTORE manifest naming the original path, owner, mode, the tarball's sha256 and the restore commands. Emptying the
# quarantine is the operator's decision and is never automated.
# Refused (exit 2), at the plan and again immediately before the move:
#   - a path that is not absolute and normalised, that does not exist, or that is a symlink or has a symlinked
#     ancestor (realpath must equal the path);
#   - a trust root, a path inside one or an ancestor of one (the list is grant_v2.TRUST_ROOTS);
#   - any ancestor up to / that a non-root user could write (not owned by root, or group/world-writable);
#   - a path holding a mount point, or one whose mounts cannot be checked (no readable /proc/mounts);
#   - a path on another filesystem than the quarantine directory (no copy+delete).
# Exit 5: the plan changed (nothing changed); 3: the archive or the move failed (the path is still in place; the
# quarantine directory holds what was written).
set -uo pipefail
set -f

P=""; APPLY=false; LEVEL=false; EXPECT=""; TAB=$(printf '\t')
QROOT="/var/backups/agentteams/quarantine"
MOUNTS="/proc/mounts"
ROOT_UID=0
ANCESTOR_TOP="/"   # the ancestor check runs up to here (always / on a host)
need(){ case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac; }
while [ $# -gt 0 ]; do case "$1" in
  --path) need "$@"; P="$2"; shift;; --expect-digest) need "$@"; EXPECT="$2"; shift;;
  --apply) APPLY=true;; --level) LEVEL=true;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

refuse(){ echo "REFUSED $P: $*"; exit 2; }
# BEGIN trust roots (identical to agentteams/vulnscan/grant_v2.py TRUST_ROOTS / TRUST_COMPONENTS)
TRUST_ROOTS="/bin /boot /dev /etc /lib /lib32 /lib64 /libx32 /proc /run /sbin /sys /usr/bin /usr/lib /usr/lib32 /usr/lib64 /usr/libexec /usr/sbin /usr/share /usr/include /var/lib /var/log /var/spool /var/backups/agentteams /var/backups/vuln-fix /root/.ssh /root/.gnupg /root/.config/agentteams /usr/local/bin /usr/local/sbin /usr/local/lib /usr/local/etc /usr/local/libexec /home/*/.config/agentteams"
TRUST_COMPONENTS=".ssh .gnupg authorized_keys sudoers sudoers.d"
# END trust roots
relation(){   # $1 path, $2 root: "in" (path is or lies under root), "contains" (root lies under path), "" otherwise
  local pp=() rr=() i n m k
  IFS=/ read -r -a pp <<< "${1#/}"; IFS=/ read -r -a rr <<< "${2#/}"
  n=${#pp[@]}; m=${#rr[@]}; k=$n; [ "$m" -lt "$k" ] && k=$m
  for ((i = 0; i < k; i++)); do case "${pp[$i]}" in ${rr[$i]}) ;; *) return 0;; esac; done   # a root part may be '*'
  if [ "$n" -ge "$m" ]; then echo in; else echo contains; fi
}
trust_problem(){   # same rule as grant_v2.trust_root_problem
  local p="$1" r c n rel
  [[ $p =~ ^/[A-Za-z0-9._@+/-]+$ ]] || { echo "must be an absolute path of letters, digits and ._@+-/ only"; return; }
  case "$p" in //*|*//*|*/./*|*/../*|*/.|*/..|*/) echo "must be normalised"; return;; esac
  local parts=(); IFS=/ read -r -a parts <<< "${p#/}"; n=${#parts[@]}
  [ "$n" -ge 2 ] || { echo "a top-level directory is never quarantined"; return; }
  [ "${parts[0]}" = home ] && [ "$n" -lt 3 ] && { echo "a home directory itself is never quarantined"; return; }
  if [ "${parts[0]}" = usr ] && { [ "$n" -lt 3 ] || [ "${parts[1]}" != local ]; }; then echo "only /usr/local/<name>/... under /usr"; return; fi
  for c in "${parts[@]}"; do for r in $TRUST_COMPONENTS; do [ "$c" = "$r" ] && { echo "names ssh/gpg/sudo material"; return; }; done; done
  for r in $TRUST_ROOTS; do
    rel=$(relation "$p" "$r")
    [ "$rel" = in ] && { echo "is or lies in the trust root $r"; return; }
    [ "$rel" = contains ] && { echo "contains the trust root $r"; return; }
  done
}
owner_mode(){ stat -c '%U:%G %a' -- "$1" 2>/dev/null || stat -f '%Su:%Sg %Lp' -- "$1"; }
uid_of(){ stat -c %u -- "$1" 2>/dev/null || stat -f %u -- "$1"; }
mode_of(){ stat -c %a -- "$1" 2>/dev/null || stat -f %Lp -- "$1"; }
dev_of(){ stat -c %d -- "$1" 2>/dev/null || stat -f %d -- "$1"; }

# BEGIN plan_digest (identical in run-host.sh and every level script)
plan_digest(){
  tr -d '\r' | LC_ALL=C grep "^PLAN$TAB" | LC_ALL=C sed -E 's/[[:space:]]+/ /g; s/ $//' | LC_ALL=C sort -u \
    | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | awk '{print $1}'
}
# END plan_digest

ancestors_problem(){   # every ancestor of P up to /: a real directory, owned by root, not group/world-writable
  local d u m
  d=$(dirname -- "$P")
  while :; do
    [ -d "$d" ] && [ ! -L "$d" ] || { echo "ancestor $d is not a real directory"; return; }
    u=$(uid_of "$d"); m=$(mode_of "$d")
    [ "$u" = "$ROOT_UID" ] || { echo "ancestor $d is owned by uid $u: a non-root user could replace what P names"; return; }
    (( 8#$m & 8#022 )) && { echo "ancestor $d is group/world-writable (mode $m)"; return; }
    [ "$d" = / ] || [ "$d" = "$ANCESTOR_TOP" ] && return
    d=$(dirname -- "$d")
  done
}
nearest(){ local d="$1"; while [ ! -e "$d" ]; do d=$(dirname -- "$d"); done; echo "$d"; }   # nearest existing ancestor
checks(){   # $1: the quarantine directory to compare filesystems with (it, or its nearest existing ancestor)
  local why; why=$(trust_problem "$P"); [ -z "$why" ] || refuse "$why"
  [ -e "$P" ] || [ -L "$P" ] || refuse "does not exist"
  [ ! -L "$P" ] || refuse "is a symlink"
  local real; real=$(realpath -e -- "$P" 2>/dev/null || readlink -f -- "$P" 2>/dev/null)
  [ "$real" = "$P" ] || refuse "resolves to ${real:-nothing} (a symlinked ancestor): realpath must equal the path"
  why=$(ancestors_problem); [ -z "$why" ] || refuse "$why"
  [ -r "$MOUNTS" ] || refuse "cannot read $MOUNTS: mount points under it cannot be ruled out"
  if awk -v p="$P" '$2 == p || index($2, p "/") == 1 {f=1} END {exit !f}' "$MOUNTS"; then refuse "holds a mount point"; fi
  [ "$(dev_of "$P")" = "$(dev_of "$1")" ] || refuse "is on another filesystem than $1: a move would copy and delete"
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
  checks "$(nearest "$QROOT")"
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
  checks "$q"   # again, immediately before the move: nothing may have changed under P since the plan
  echo "+ mv -T -- $P $q/payload"
  mv -T -- "$P" "$q/payload" || { echo "FAIL move failed — see $q"; exit 3; }
  [ ! -e "$P" ] && [ -e "$q/payload" ] || { echo "FAIL $P still present after the move — see $q"; exit 3; }
  printf 'QUARANTINED\t%s\t%s\n' "$P" "$q"
}

main < /dev/null
