#!/usr/bin/env bash
# L4 — Upgrade a running Python app's vulnerable packages, restart it, health-check
# it, and roll back automatically on failure. DRY RUN by default.
#
#   ./remote.sh <host> L4-python-app.sh --unit myapp.service --dir '~deploy/myapp' \
#        --python '~deploy/myapp/.venv/bin/python' --as deploy \
#        --pin somepackage==1.2.3 --health http://127.0.0.1:8000/health  [--apply]
#
# (apps.conf + run-host.sh build these command lines for you.)
#
# Options
#   --unit NAME          systemd unit to restart (required)
#   --dir PATH           app directory (required; "~user/sub" is resolved on the server)
#   --python PATH        interpreter that runs the app (venv python, or system python3)
#   --as USER            run pip/git as this user (default: owner of --dir)
#   --user-site          packages live in the user's ~/.local (pip --user)
#   --system-pip         allow pip into a system interpreter (adds --break-system-packages
#                        where the distro marks it EXTERNALLY-MANAGED)
#   --pin SPEC           package==version to install (repeatable). Preferred: exactly
#                        the versions from the reviewed requirements PR (script 03).
#   --requirements FILE  install a requirements file instead/in addition (relative to --dir)
#   --allow-sdist        allow building from source distributions (runs package build code as
#                        the install user); default is wheels only (--only-binary :all:)
#   --git-pull           git pull --ff-only in --dir first (refused if the checkout is dirty).
#                        Careful: this deploys EVERYTHING merged since the last deploy.
#   --health URL         HTTP(S) health URL (self-signed OK); default: unit must stay active
#   --apply              do it
#   --level              server-apply l4-pins: print the plan (PLAN lines) and pre-state; refuses --git-pull,
#                        --allow-sdist and --requirements; a pin crossing a major version is refused unless
#   --major-allowed      (given by run-host only for a unit the signed grant allows) is set
#   --expect-digest SHA  with --level --apply: the plan's sha256, recomputed here before any change (exit 5 if not)
#
# Rollback restores the pre-change `pip freeze` and (if pulled) the previous git HEAD. Under --level a rollback exits
# 6 (restored and healthy again) or 7 (not restored): the host is stopped for the operator.
set -uo pipefail

UNIT=""; DIR=""; PY=""; AS=""; USERSITE=false; SYSPIP=false; PINS=(); REQ=""; PULL=false; HEALTH=""; APPLY=false; SDIST=false
LEVEL=false; EXPECT=""; MAJOR=false; TAB=$(printf '\t')
need(){ case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac; }   # missing value: usage error (2), never an unbound-variable crash
while [ $# -gt 0 ]; do case "$1" in
  --unit) need "$@"; UNIT="$2"; shift;; --dir) need "$@"; DIR="$2"; shift;; --python) need "$@"; PY="$2"; shift;; --as) need "$@"; AS="$2"; shift;;
  --user-site) USERSITE=true;; --system-pip) SYSPIP=true;; --pin) need "$@"; PINS+=("$2"); shift;;
  --requirements) need "$@"; REQ="$2"; shift;; --git-pull) PULL=true;; --health) need "$@"; HEALTH="$2"; shift;; --apply) APPLY=true;;
  --allow-sdist) SDIST=true;;
  --level) LEVEL=true;; --major-allowed) MAJOR=true;; --expect-digest) need "$@"; EXPECT="$2"; shift;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
if $LEVEL; then
  if $PULL || $SDIST || [ -n "$REQ" ]; then echo "refused: --level never takes --git-pull, --allow-sdist or --requirements" >&2; exit 2; fi
  if $APPLY && ! [[ $EXPECT =~ ^[0-9a-f]{64}$ ]]; then echo "refused: --level --apply needs --expect-digest" >&2; exit 2; fi
elif [ -n "$EXPECT" ] || $MAJOR; then echo "refused: --expect-digest/--major-allowed need --level" >&2; exit 2; fi

say(){ printf '\n== %s\n' "$*"; }
die(){ echo "FAIL   $*"; if $LEVEL; then exit 3; fi; exit 1; }   # --level: a failed step (3), never "action needed"
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
major_of(){ local v="${1#*!}"; v="${v%%[!0-9]*}"; echo "${v:-0}"; }
level_plan(){   # PLAN lines per pin: unit, package, installed (or -), pinned; refuses an unallowed major jump
  local p name want have
  for p in ${PINS[@]+"${PINS[@]}"}; do
    name="${p%%==*}"; name="${name%%\[*}"; want="${p##*==}"
    have=$("${pip[@]}" show "$name" 2>/dev/null | awk '/^Version:/{print $2}')
    if [ -n "$have" ] && [ "$(major_of "$have")" != "$(major_of "$want")" ] && ! $MAJOR; then
      echo "REFUSED $name $have -> $want crosses a major version; the signed grant does not allow it for $UNIT" >&2
      return 2
    fi
    printf 'PLAN%s%s%s%s%s%s%s%s%s%s\n' "$TAB" pins "$TAB" "$UNIT" "$TAB" "$name" "$TAB" "${have:--}" "$TAB" "$want"
  done
}

main(){
  local p
  for p in ${PINS[@]+"${PINS[@]}"}; do   # a pin must be name==version: never a pip option or URL
    [[ "$p" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9.+!_-]+$ ]] || die "invalid --pin '$p' (expected name==version)"
  done
  [ -n "$UNIT" ] && [ -n "$DIR" ] || die "--unit and --dir are required"
  DIR=$(expand_tilde "$DIR"); PY=$(expand_tilde "$PY")
  [ -d "$DIR" ] || die "no such dir $DIR"
  systemctl cat "$UNIT" >/dev/null 2>&1 || die "unit $UNIT not found"
  [ -n "$AS" ] || AS=$(stat -c %U "$DIR")
  [ -n "$PY" ] || PY=$(command -v python3)
  [ -x "$PY" ] || die "python $PY not executable"
  [ ${#PINS[@]} -gt 0 ] || [ -n "$REQ" ] || $PULL || die "nothing to install: give --pin, --requirements or --git-pull"

  AS_HOME=$(getent passwd "$AS" | cut -d: -f6)
  local as=(runuser -u "$AS" -- env HOME="$AS_HOME") pipflags=(--disable-pip-version-check --no-input --prefer-binary --upgrade-strategy only-if-needed)
  $USERSITE && pipflags+=(--user)
  $SDIST || pipflags+=(--only-binary :all:)
  if ! $USERSITE && "$PY" -c 'import sys;sys.exit(0 if sys.prefix==sys.base_prefix else 1)' 2>/dev/null; then
    $SYSPIP || die "$PY is a system interpreter, not a venv — pass --system-pip to confirm (or --user-site)"
    ls "$("$PY" -c 'import sysconfig;print(sysconfig.get_path("stdlib"))')"/EXTERNALLY-MANAGED >/dev/null 2>&1 && pipflags+=(--break-system-packages)
    as=()   # system site-packages are root-owned
  fi
  local pip=(${as[@]+"${as[@]}"} "$PY" -m pip)

  echo "host: $(hostname)  unit: $UNIT  dir: $DIR  python: $PY ($("$PY" -V 2>&1))  as: $AS  mode: $($APPLY && echo APPLY || echo DRY-RUN)"
  local lplan=""
  if $LEVEL; then
    lplan=$(level_plan) || exit 2
    printf '%s\n' "$lplan"
    if ! $APPLY; then "${pip[@]}" freeze 2>/dev/null | sed "s/^/PRESTATE${TAB}pip${TAB}${UNIT}${TAB}/"; fi
    if $APPLY && [ "$(printf '%s\n' "$lplan" | plan_digest)" != "$EXPECT" ]; then
      echo "DIGEST MISMATCH: the plan for $UNIT changed since it was confirmed — nothing changed"; exit 5
    fi
  fi
  say "Current versions of the targeted packages"
  for p in ${PINS[@]+"${PINS[@]}"}; do "${pip[@]}" show "${p%%[=<>!~]*}" 2>/dev/null | awk '/^(Name|Version|Location):/{printf "%s ", $2} END{print ""}'; done

  say "Baseline health (before any change)"
  if [ -n "$HEALTH" ]; then
    curl -fsSk --max-time 5 -o /dev/null "$HEALTH" && echo "OK     $HEALTH answers now" \
      || die "$HEALTH fails BEFORE any change — fix the URL in apps.conf (or use '-') so a good upgrade isn't rolled back"
  else systemctl is-active --quiet "$UNIT" && echo "OK     $UNIT active now" || die "$UNIT is not active before any change"; fi
  local ports_before=""; $LEVEL && ports_before=$(unit_ports "$UNIT")

  local prev_head=""
  if $PULL; then
    say "git"
    git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1 || die "$DIR is not a git checkout; drop --git-pull"
    [ -z "$(runuser -u "$AS" -- env HOME="$AS_HOME" git -C "$DIR" status --porcelain --untracked-files=no)" ] || die "$DIR has uncommitted changes — refusing to pull"
    prev_head=$(git -C "$DIR" rev-parse HEAD)
    if $APPLY; then
      runuser -u "$AS" -- env HOME="$AS_HOME" git -C "$DIR" fetch -q origin
    else
      echo "dry run: not fetching (that would write to the repository); comparing with the last-fetched upstream"
    fi
    echo "fast-forward $(git -C "$DIR" rev-parse --short HEAD) → $(git -C "$DIR" rev-parse --short '@{u}'):"
    git -C "$DIR" log --oneline HEAD..'@{u}' | head -30
  fi

  local args=("${pipflags[@]}")
  [ -n "$REQ" ] && args+=(-r "$DIR/$REQ")
  args+=(${PINS[@]+"${PINS[@]}"})

  if ! $APPLY; then
    say "pip dry run"
    "${pip[@]}" install --dry-run "${args[@]}" 2>&1 | grep -E 'Would install|ERROR|error|conflict' || echo "(nothing to change, or pip < 22.2 without --dry-run; would run: pip install ${args[*]})"
    printf '\nDRY RUN — nothing changed. Re-run with --apply.\n'; return 0
  fi

  local bak=/var/backups/vuln-fix; install -d -m 700 "$bak"
  local freeze="$bak/${UNIT%.service}-$(date -u +%Y%m%d%H%M%S).freeze"
  if $USERSITE; then "${pip[@]}" freeze --user > "$freeze"; else "${pip[@]}" freeze > "$freeze"; fi || die "pip freeze failed"
  echo "backup: $freeze"

  rollback(){
    say "ROLLBACK"
    [ -n "$prev_head" ] && runuser -u "$AS" -- env HOME="$AS_HOME" git -C "$DIR" reset -q --hard "$prev_head" && echo "git reset to $prev_head"
    "${pip[@]}" install "${pipflags[@]}" -r "$freeze" >/dev/null 2>&1 && echo "packages restored from $freeze" || echo "FAIL restoring packages — do it by hand: ${pip[*]} install -r $freeze"
    systemctl restart "$UNIT"; sleep 5
    if $LEVEL; then   # server-apply: restored and healthy (6) or not (7); either way the host stops here
      if unit_healthy "$UNIT" "$ports_before"; then echo "REVERTED $UNIT to its pre-state"; exit 6; fi
      echo "FAIL $UNIT unhealthy after rollback — journalctl -u $UNIT -n 80"; exit 7
    fi
    systemctl is-active --quiet "$UNIT" && echo "$UNIT active after rollback" || echo "FAIL $UNIT still down — journalctl -u $UNIT -n 80"
    exit 1
  }

  if $PULL; then runuser -u "$AS" -- env HOME="$AS_HOME" git -C "$DIR" merge -q --ff-only '@{u}' || rollback; fi
  say "pip install"
  "${pip[@]}" install "${args[@]}" || rollback
  "${pip[@]}" check || echo "WARN   pip check reports conflicts (above) — continuing to health check"

  say "Restart + health"
  systemctl restart "$UNIT" || rollback
  local i okc=0
  for i in $(seq 1 10); do
    sleep 2
    systemctl is-active --quiet "$UNIT" || { echo "unit not active"; rollback; }
    if [ -n "$HEALTH" ]; then curl -fsSk --max-time 5 -o /dev/null "$HEALTH" && okc=$((okc+1))
    else okc=$((okc+1)); fi
    [ "$okc" -ge 3 ] && break
  done
  [ "$okc" -ge 3 ] || { echo "health check failed: ${HEALTH:-unit stability}"; rollback; }
  if $LEVEL && [ -z "$HEALTH" ]; then unit_healthy "$UNIT" "$ports_before" || rollback; fi   # + port probe
  echo "OK     $UNIT healthy on new packages"
  for p in ${PINS[@]+"${PINS[@]}"}; do "${pip[@]}" show "${p%%[=<>!~]*}" 2>/dev/null | awk '/^(Name|Version):/{printf "%s ", $2} END{print ""}'; done
}

main < /dev/null
