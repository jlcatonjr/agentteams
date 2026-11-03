#!/usr/bin/env bash
# L6 — server-apply level py-venv: build a NEW venv beside an app's venv from its apps.conf pin set, switch to it
# atomically, restart and health-check the app, and switch back automatically on failure. The old venv is kept.
# Runs only under --level (run-host.sh builds the command line from an apps.conf entry whose flags carry --venv-base).
#
#   ./remote.sh <host> L6-py-venv.sh --level --unit app.service --dir '~deploy/app' \
#        --python '~deploy/app/.venv/bin/python' --venv-base /usr/bin/python3.11 [--distro-pkg python3.11] \
#        [--as deploy] [--health URL] --pin name==version ... [--major-allowed]            # the plan
#   ... --apply --expect-digest SHA                                                       # apply exactly that plan
#
# The new venv holds the old venv's `pip freeze` with each pinned package replaced by its pin (wheels only). The
# switch: when the venv path is a symlink, one rename of a new symlink over it (atomic); when it is a directory, it
# is renamed to <venv>.agentteams-prev-<ts> and a symlink to the new venv takes its place. Nothing is deleted.
# Exit: 0 done · 2 refused/usage · 3 failed before the switch (the old venv untouched) · 5 the plan changed
# (nothing changed) · 6 health failed and the old venv is back and healthy · 7 health failed and it is not.
set -uo pipefail
set -f

UNIT=""; DIR=""; PY=""; AS=""; BASE=""; DPKG=""; PINS=(); HEALTH=""; APPLY=false; LEVEL=false; EXPECT=""; MAJOR=false
TAB=$(printf '\t')
need(){ case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac; }
while [ $# -gt 0 ]; do case "$1" in
  --unit) need "$@"; UNIT="$2"; shift;; --dir) need "$@"; DIR="$2"; shift;; --python) need "$@"; PY="$2"; shift;;
  --as) need "$@"; AS="$2"; shift;; --venv-base) need "$@"; BASE="$2"; shift;; --distro-pkg) need "$@"; DPKG="$2"; shift;;
  --pin) need "$@"; PINS+=("$2"); shift;; --health) need "$@"; HEALTH="$2"; shift;;
  --expect-digest) need "$@"; EXPECT="$2"; shift;;
  --apply) APPLY=true;; --level) LEVEL=true;; --major-allowed) MAJOR=true;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

say(){ printf '\n== %s\n' "$*"; }
die(){ echo "FAIL   $*"; exit "${2:-3}"; }
expand_tilde(){  # "~user/x" -> that user's home + "/x" (no eval)
  case "$1" in
    "~"*) local u="${1%%/*}"; u="${u#\~}"; local rest="${1#*/}"; [ "$rest" = "$1" ] && rest=""
          local h; h=$(getent passwd "${u:-root}" | cut -d: -f6); echo "${h}${rest:+/$rest}";;
    *) echo "$1";;
  esac
}

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
  local u="$1" before="$2" i ok=0 now p missing=""
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
major_of(){ local v="${1#*!}"; v="${v%%[!0-9]*}"; echo "${v:-0}"; }
healthy(){   # the app's health URL when apps.conf gives one, else active + its ports listening again
  if [ -n "$HEALTH" ]; then
    local i okc=0
    for i in 1 2 3 4 5 6 7 8 9 10; do
      sleep 2
      systemctl is-active --quiet "$UNIT" || return 1
      curl -fsSk --max-time 5 -o /dev/null "$HEALTH" && okc=$((okc+1))
      [ "$okc" -ge 3 ] && return 0
    done
    return 1
  fi
  unit_healthy "$UNIT" "$PORTS"
}

plan(){
  local p name want have
  if [ -n "$DPKG" ]; then
    local st=install
    if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f '${Status}' "$DPKG" 2>/dev/null | grep -q 'install ok installed' && st=installed
    elif command -v rpm >/dev/null 2>&1; then rpm -q "$DPKG" >/dev/null 2>&1 && st=installed; fi
    printf 'PLAN\tvenv\t%s\tdistro\t%s\t%s\n' "$UNIT" "$DPKG" "$st"
  fi
  printf 'PLAN\tvenv\t%s\tbase\t%s\n' "$UNIT" "$BASE"
  for p in ${PINS[@]+"${PINS[@]}"}; do
    name="${p%%==*}"; name="${name%%\[*}"; want="${p##*==}"
    have=$("${ASU[@]}" "$PY" -m pip show "$name" 2>/dev/null | awk '/^Version:/{print $2}')
    if [ -n "$have" ] && [ "$(major_of "$have")" != "$(major_of "$want")" ] && ! $MAJOR; then
      echo "REFUSED $name $have -> $want crosses a major version; the signed grant does not allow it for $UNIT" >&2
      return 2
    fi
    printf 'PLAN\tvenv\t%s\tpin\t%s\t%s\t%s\n' "$UNIT" "$name" "${have:--}" "$want"
  done
  printf 'PLAN\tvenv\t%s\tswitch\t%s\n' "$UNIT" "$VENV"
}

main(){
  $LEVEL || die "L6 runs only under --level (server-apply py-venv)" 2
  local p
  for p in ${PINS[@]+"${PINS[@]}"}; do
    [[ "$p" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9.+!_-]+$ ]] || die "invalid --pin '$p' (expected name==version)" 2
  done
  [ ${#PINS[@]} -gt 0 ] || die "nothing to install: give --pin" 2
  [ -n "$UNIT" ] && [ -n "$DIR" ] && [ -n "$PY" ] && [ -n "$BASE" ] || die "--unit, --dir, --python and --venv-base are required" 2
  [ -z "$DPKG" ] || [[ $DPKG =~ ^[a-z0-9][a-z0-9.+-]*$ ]] || die "invalid --distro-pkg '$DPKG'" 2
  if $APPLY && ! [[ $EXPECT =~ ^[0-9a-f]{64}$ ]]; then die "--apply needs --expect-digest" 2; fi
  DIR=$(expand_tilde "$DIR"); PY=$(expand_tilde "$PY")
  VENV=$(dirname "$(dirname "$PY")")
  [ -f "$VENV/pyvenv.cfg" ] || die "$VENV is not a venv (no pyvenv.cfg)" 2
  systemctl cat "$UNIT" >/dev/null 2>&1 || die "unit $UNIT not found" 2
  [ -n "$AS" ] || AS=$(stat -c %U "$DIR")
  AS_HOME=$(getent passwd "$AS" | cut -d: -f6)
  [ -n "$AS_HOME" ] || die "no such user $AS" 2
  # The app's venv is writable by the app user: its interpreter never runs as root (pip show/freeze included).
  ASU=(runuser -u "$AS" -- env HOME="$AS_HOME")
  trap 'rm -f -- "$VENV.agentteams-tmp"' EXIT   # a switch interrupted between ln and mv leaves no stray link
  echo "host: $(hostname)  unit: $UNIT  venv: $VENV  base: $BASE  as: $AS  mode: $($APPLY && echo APPLY || echo PLAN)"
  local lplan; lplan=$(plan) || exit 2
  printf '%s\n' "$lplan"
  if ! $APPLY; then
    "${ASU[@]}" "$PY" -m pip freeze 2>/dev/null | sed "s/^/PRESTATE${TAB}venv${TAB}${UNIT}${TAB}/"
    printf 'PRESTATE\tvenv\t%s\tpath %s -> %s\n' "$UNIT" "$VENV" "$(readlink "$VENV" 2>/dev/null || echo directory)"
    printf '\nPLAN ONLY — nothing changed.\n'; return 0
  fi
  [ "$(printf '%s\n' "$lplan" | plan_digest)" = "$EXPECT" ] || { echo "DIGEST MISMATCH: the plan for $UNIT changed — nothing changed"; exit 5; }
  systemctl is-active --quiet "$UNIT" || die "$UNIT is not active before any change"
  PORTS=$(unit_ports "$UNIT")

  if [ -n "$DPKG" ] && printf '%s\n' "$lplan" | grep -q "${TAB}distro${TAB}.*${TAB}install$"; then
    say "Installing the distro package $DPKG"
    if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get -y install "$DPKG" || die "apt-get install $DPKG"
    elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
      "$(command -v dnf || command -v yum)" -y install "$DPKG" || die "install $DPKG"
    else die "no apt-get, dnf or yum to install $DPKG"; fi
  fi
  [ -x "$BASE" ] || die "base interpreter $BASE is not executable"
  local ts; ts=$(date -u +%Y%m%d%H%M%S)
  local new="$VENV.agentteams-$ts" as=("${ASU[@]}")
  say "Building $new"
  "${as[@]}" "$BASE" -m venv "$new" || die "venv creation failed (old venv untouched)"
  local req="$new/agentteams-requirements.txt" names=() n
  for p in "${PINS[@]}"; do n="${p%%==*}"; names+=("$(printf '%s' "${n%%\[*}" | tr 'A-Z_.' 'a-z--')"); done
  { "${ASU[@]}" "$PY" -m pip freeze 2>/dev/null | grep -E '^[A-Za-z0-9][A-Za-z0-9._-]*==' | while IFS= read -r line; do
      n=$(printf '%s' "${line%%==*}" | tr 'A-Z_.' 'a-z--'); keep=true
      for x in "${names[@]}"; do [ "$n" = "$x" ] && keep=false; done
      $keep && printf '%s\n' "$line"
    done; printf '%s\n' "${PINS[@]}"; } > "$req" || die "writing $req"
  "${as[@]}" "$new/bin/python" -m pip install --disable-pip-version-check --no-input --only-binary :all: -r "$req" \
    || die "pip install into $new failed (old venv untouched)"
  "${as[@]}" "$new/bin/python" -m pip check || echo "WARN   pip check reports conflicts (above) — continuing to health check"

  say "Switch"
  local old was_link=false
  if [ -L "$VENV" ]; then
    was_link=true; old=$(readlink "$VENV")
    ln -s "$new" "$VENV.agentteams-tmp" && mv -T "$VENV.agentteams-tmp" "$VENV" || die "switch failed (old venv still active)"
  else
    old="$VENV.agentteams-prev-$ts"
    mv -T "$VENV" "$old" || die "switch failed (old venv still active)"
    ln -s "$new" "$VENV" || { mv -T "$old" "$VENV"; die "switch failed (old venv restored)"; }
  fi
  echo "switched $VENV -> $new (old kept: $old)"
  revert(){
    say "REVERT"
    if $was_link; then ln -s "$old" "$VENV.agentteams-tmp" && mv -T "$VENV.agentteams-tmp" "$VENV"
    else rm -f -- "$VENV" && mv -T "$old" "$VENV"; fi || { echo "FAIL could not switch $VENV back to $old"; exit 7; }
    systemctl restart "$UNIT"
    if healthy; then echo "REVERTED $UNIT to its old venv"; exit 6; fi
    echo "FAIL $UNIT unhealthy after the revert — journalctl -u $UNIT -n 80"; exit 7
  }
  say "Restart + health"
  systemctl restart "$UNIT" || revert
  healthy || { echo "health check failed: ${HEALTH:-active + listening ports}"; revert; }
  echo "OK     $UNIT healthy on $new (old venv kept at $old; removing it is the operator's decision)"
}

main < /dev/null
