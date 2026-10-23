#!/usr/bin/env bash
# L3 — Upgrade every Node.js runtime on the host to a patched release.
# DRY RUN by default.
#
#   ./remote.sh <host> L3-node-runtime.sh                       # report + plan
#   ./remote.sh <host> L3-node-runtime.sh --apply               # newest release of each install's major line
#   ./remote.sh <host> L3-node-runtime.sh --apply --major 24    # move every install to the newest 24.x
#
# Which lines are end-of-life, and which releases are security releases, is decided on
# the workstation (`vulnscan inventory`) from live data; this script only knows
# "newest release of a line" (nodejs.org/dist/index.json).
#   ... --restart-services                                       # restart node systemd units / pm2 after
#   ... --cooldown-days N (default 14)   only adopt releases at least N days old (Rule S-10)
#   ... --allow-fresh                    adopt the newest release even inside the cooldown — only
#                                        for a security release with a published advisory
#   ... --require-gpg                    fail unless the SHASUMS256 GPG signature verifies
#
# Handles: official tarballs (/usr/local, /opt — SHA-256 verified against
# nodejs.org SHASUMS256.txt), nvm installs (per owning user), NodeSource apt/dnf
# repos (major switch rewrites the repo file). Distro-packaged Node gets
# patched by L2-os-patch.sh; distro major switches are printed, not automated.
# Containers: use L5-containers.sh.
set -uo pipefail

APPLY=false; MAJOR=""; RESTART=false; COOLDOWN=14; FRESH=false; NEEDGPG=false
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --major) MAJOR="$2"; shift;; --restart-services) RESTART=true;;
  --cooldown-days) COOLDOWN="$2"; shift;; --allow-fresh) FRESH=true;; --require-gpg) NEEDGPG=true;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

SCAN_ROOTS="/usr/bin /usr/local /opt /home /root"
ARCH=$(uname -m); case "$ARCH" in x86_64) NARCH=x64;; aarch64|arm64) NARCH=arm64;; *) NARCH=$ARCH;; esac
CHANGED=false

say(){ printf '\n== %s\n' "$*"; }
run(){ if $APPLY; then echo "+ $*"; "$@"; else echo "WOULD  $*"; fi; }
vercmp(){ awk -v a="${1#v}" -v b="${2#v}" 'BEGIN{n=split(a,x,/[.+~-]/);m=split(b,y,/[.+~-]/);k=(n>m?n:m)
  for(i=1;i<=k;i++){xi=x[i]+0;yi=y[i]+0;if(xi<yi){print -1;exit}if(xi>yi){print 1;exit}}print 0}'; }
pkg_owner(){ if command -v dpkg >/dev/null 2>&1; then dpkg -S "$1" 2>/dev/null | head -1 | cut -d: -f1
  elif command -v rpm >/dev/null 2>&1; then rpm -qf "$1" 2>/dev/null | grep -v 'not owned' | head -1; fi; }

INDEX=""
latest_of(){  # newest release of major $1 that is at least $COOLDOWN days old (or newest, with --allow-fresh)
  [ -n "$INDEX" ] || INDEX=$(curl -fsSL --max-time 30 https://nodejs.org/dist/index.json) || { echo ""; return; }
  local cutoff; cutoff=$(date -u -d "-$COOLDOWN days" +%F 2>/dev/null || date -u -v-"$COOLDOWN"d +%F)
  $FRESH && cutoff=9999-12-31
  echo "$INDEX" | tr '{' '\n' | grep -E '"version":"v'"$1"'\.[0-9]+\.[0-9]+"' \
    | sed -nE 's/.*"version":"v([0-9.]+)".*"date":"([0-9-]+)".*/\2 \1/p' \
    | awk -v c="$cutoff" '$1 <= c {print $2; exit}'
}

install_tarball(){  # $1=current binary  $2=target version
  local bin="$1" ver="$2" base prefix tmp tgz
  base="node-v${ver}-linux-${NARCH}"; tgz="${base}.tar.xz"
  # prefix = dir containing bin/node (e.g. /usr/local or /opt/node-v24.x-linux-x64)
  prefix=$(dirname "$(dirname "$bin")")
  echo "tarball install at prefix $prefix → v$ver"
  $APPLY || { echo "WOULD  download https://nodejs.org/dist/v$ver/$tgz, verify SHA-256 against SHASUMS256.txt, install"; return; }
  tmp=$(mktemp -d)
  curl -fsSL --max-time 300 -o "$tmp/$tgz" "https://nodejs.org/dist/v$ver/$tgz" \
    && curl -fsSL --max-time 30 -o "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/v$ver/SHASUMS256.txt" || { echo "FAIL download"; rm -rf "$tmp"; return 1; }
  (cd "$tmp" && grep " $tgz\$" SHASUMS256.txt | sha256sum -c -) || { echo "FAIL checksum mismatch — NOT installing"; rm -rf "$tmp"; return 1; }
  if command -v gpg >/dev/null 2>&1 && curl -fsSL --max-time 30 -o "$tmp/SHASUMS256.txt.sig" "https://nodejs.org/dist/v$ver/SHASUMS256.txt.sig" 2>/dev/null; then
    if gpg --batch --verify "$tmp/SHASUMS256.txt.sig" "$tmp/SHASUMS256.txt" >/dev/null 2>&1; then echo "OK     GPG signature verified"
    elif $NEEDGPG; then echo "FAIL   GPG signature did not verify — NOT installing (import the Node.js release keys)"; rm -rf "$tmp"; return 1
    else echo "INFO   GPG signature not verified (release keys not imported) — SHA-256 over HTTPS still checked"; fi
  elif $NEEDGPG; then echo "FAIL   --require-gpg but gpg or the signature is unavailable"; rm -rf "$tmp"; return 1
  fi
  local bak="/var/backups/node-before-$ver-$(date -u +%Y%m%d%H%M%S)"
  mkdir -p "$bak"
  case "$prefix" in
    /usr/local)  # classic "tar -C /usr/local --strip-components=1" layout
      cp -a /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx "$bak"/ 2>/dev/null
      cp -a /usr/local/lib/node_modules/npm "$bak/npm-module" 2>/dev/null
      tar -xJf "$tmp/$tgz" -C /usr/local --strip-components=1 --no-same-owner \
        --exclude="$base/CHANGELOG.md" --exclude="$base/LICENSE" --exclude="$base/README.md" ;;
    *)           # self-contained dir: install alongside, then repoint any symlinks to it
      mkdir -p /opt/nodejs && tar -xJf "$tmp/$tgz" -C /opt/nodejs --no-same-owner
      local new="/opt/nodejs/$base" l
      for l in /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx /usr/bin/node; do
        [ -L "$l" ] && readlink -f "$l" | grep -q "^$prefix/" && { cp -a "$l" "$bak/"; ln -sfn "$new/bin/$(basename "$l")" "$l"; echo "relinked $l → $new/bin/$(basename "$l")"; }
      done
      echo "NOTE   old tree $prefix left in place; units that call $prefix/bin/node directly must be edited to $new/bin/node" ;;
  esac
  rm -rf "$tmp"; CHANGED=true
  echo "backup of replaced binaries: $bak"
}

handle_binary(){
  local bin="$1" v maj owner target tmaj user
  v=$("$bin" -v 2>/dev/null) || return; v=${v#v}; maj=${v%%.*}
  owner=$(pkg_owner "$bin")
  tmaj=${MAJOR:-$maj}
  target=$(latest_of "$tmaj"); [ -n "$target" ] || { echo "FAIL   no $tmaj.x release older than the ${COOLDOWN}-day cooldown (or nodejs.org unreachable); --allow-fresh for a security release"; return; }
  if [ "$tmaj" = "$maj" ] && [ "$(vercmp "$v" "$target")" -ge 0 ]; then echo "OK     $bin v$v (newest $maj.x)"; return; fi
  echo "UPGRADE $bin v$v → v$target"
  case "$bin" in
    */.nvm/versions/node/*)
      user=$(stat -c %U "$bin")
      run runuser -u "$user" -- env HOME="$(getent passwd "$user" | cut -d: -f6)" bash -lc "source ~/.nvm/nvm.sh && nvm install $target && nvm alias default $target"
      $APPLY && CHANGED=true
      echo "NOTE   units/pm2 that call the old nvm path directly must be pointed at v$target" ;;
    *)
      if [ -n "$owner" ]; then
        if ls /etc/apt/sources.list.d/nodesource* /etc/yum.repos.d/nodesource* >/dev/null 2>&1; then
          if [ "$tmaj" != "$maj" ]; then
            local f; for f in /etc/apt/sources.list.d/nodesource* /etc/yum.repos.d/nodesource*; do
              [ -f "$f" ] && run sed -i.bak -E "s/(node|pub)_${maj}\.x/\1_${tmaj}.x/g" "$f"; done
          fi
          if command -v apt-get >/dev/null 2>&1; then run apt-get update -q; run apt-get install -y nodejs
          else run dnf -y --refresh install nodejs; fi
          $APPLY && CHANGED=true
        elif [ "$tmaj" = "$maj" ]; then echo "distro package $owner — patched by L2-os-patch.sh (security fix arrives via the distro)"
        else echo "distro package $owner on EOL line $maj — switch manually, e.g. Amazon Linux 2023: dnf install nodejs${tmaj} && alternatives --config node;"
             echo "       Ubuntu: add NodeSource node_${tmaj}.x repo (https://github.com/nodesource/distributions) then apt-get install nodejs"; fi
      else install_tarball "$bin" "$target"; fi ;;
  esac
}

restart_node_services(){
  say "Node services"
  local u ex any=false
  for u in $(systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'); do
    ex=$(systemctl show -p ExecStart --value "$u" 2>/dev/null)
    echo "$ex" | grep -qE '(/|=)(node|nodejs|npm|npx|pm2)( |;|$)|/bin/node' || continue
    any=true
    if $RESTART && $CHANGED; then run systemctl restart "$u"; sleep 3; systemctl is-active --quiet "$u" && echo "OK     $u active" || echo "FAIL   $u not active — journalctl -u $u -n 50"
    else echo "NEEDS  restart: $u"; fi
  done
  local h; for h in /root /home/*; do
    [ -d "$h/.pm2" ] || continue; any=true
    local usr; usr=$(stat -c %U "$h")
    if $RESTART && $CHANGED; then run runuser -u "$usr" -- env HOME="$h" bash -lc 'pm2 update && pm2 reload all && pm2 save'
    else echo "NEEDS  pm2 reload for $usr (pm2 update && pm2 reload all)"; fi
  done
  $any || echo "no node services found"
  command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}} {{.Image}}' | sed 's/^/INFO   container (runtimes inside images: L5-containers.sh): /'
}

main(){
  echo "host: $(hostname)  mode: $($APPLY && echo APPLY || echo DRY-RUN)${MAJOR:+  --major $MAJOR}"
  say "Node binaries"
  local b found=false
  for b in $( { which -a node nodejs 2>/dev/null; find $SCAN_ROOTS -xdev -maxdepth 8 -path '*/node_modules' -prune -o -type f -name node -path '*/bin/node' -print 2>/dev/null; } | xargs -r -n1 readlink -f 2>/dev/null | sort -u ); do
    [ -x "$b" ] && { found=true; handle_binary "$b"; }
  done
  $found || echo "no Node binaries on this host"
  restart_node_services
  $APPLY || printf '\nDRY RUN — nothing changed. Re-run with --apply.\n'
}

main "$@" < /dev/null
