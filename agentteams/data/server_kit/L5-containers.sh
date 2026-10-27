#!/usr/bin/env bash
# L5 — Refresh Docker container images (node / php / python / app images such as
# Uptime Kuma pick up runtime fixes only when their image is re-pulled).
# DRY RUN by default.
#
#   ./remote.sh <host> L5-containers.sh            # list containers, check for newer images
#   ./remote.sh <host> L5-containers.sh --apply    # pull + recreate Compose-managed containers
#   ... --cooldown-days N (default 0)   refuse to recreate on an image built less than N days ago
#                                       (Rule S-10's cooldown is deprecated since 2026-10-26: no wait by default)
#   ... --allow-fresh                   recreate anyway when a --cooldown-days is set
#
# Compose projects (com.docker.compose.* labels) are recreated with
# `docker compose pull && up -d`. Standalone `docker run` containers are never
# recreated automatically (their run flags can't be reconstructed safely): the
# script pulls the image and prints the steps. A floating tag re-pulled today can
# carry a compromised release; pin a reviewed digest where that matters.
set -uo pipefail
APPLY=false; COOLDOWN=0; FRESH=false
need(){ case "${2-}" in ""|--*) echo "option $1 needs a value" >&2; exit 2;; esac; }   # missing value: usage error (2), never an unbound-variable crash
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=true;; --cooldown-days) need "$@"; COOLDOWN="$2"; shift;; --allow-fresh) FRESH=true;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done
[ "$COOLDOWN" = 0 ] || echo "note: --cooldown-days is deprecated (Rule S-10's cooldown, 2026-10-26); honoured as an explicit opt-in" >&2

main(){
  command -v docker >/dev/null 2>&1 || { echo "no docker on $(hostname)"; return 0; }
  echo "host: $(hostname)  mode: $($APPLY && echo APPLY || echo DRY-RUN)"
  local DC=(docker compose); docker compose version >/dev/null 2>&1 || DC=(docker-compose)
  local seen="" c img id proj wd files svc newid
  while read -r c img id; do
    [ -n "$c" ] || continue
    printf '\n== %s  (%s)\n' "$c" "$img"
    proj=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$c" 2>/dev/null)
    wd=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$c" 2>/dev/null)
    files=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$c" 2>/dev/null)
    svc=$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.service" }}' "$c" 2>/dev/null)
    echo "image created: $(docker image inspect -f '{{.Created}}' "$id" 2>/dev/null | cut -c1-10)"
    if ! $APPLY; then
      echo "WOULD  docker pull $img$( [ -n "$proj" ] && echo "  then: docker compose -p $proj up -d $svc" )"
      continue
    fi
    docker pull -q "$img" >/dev/null || { echo "FAIL   pull $img"; continue; }
    newid=$(docker image inspect -f '{{.Id}}' "$img" 2>/dev/null)
    if [ "$newid" = "$id" ]; then echo "OK     already newest"; continue; fi
    created=$(docker image inspect -f '{{.Created}}' "$img" 2>/dev/null | cut -c1-10)
    cutoff=$(date -u -d "-$COOLDOWN days" +%F 2>/dev/null || date -u -v-"$COOLDOWN"d +%F)
    if ! $FRESH && [ -n "$created" ] && [[ "$created" > "$cutoff" ]]; then
      echo "HOLD   newer $img was built $created (< ${COOLDOWN}-day cooldown) — not recreating; pin a reviewed digest or use --allow-fresh"
      continue
    fi
    if [ -n "$proj" ] && [ -n "$wd" ]; then
      case " $seen " in *" $proj/$svc "*) continue;; esac; seen="$seen $proj/$svc"
      local fargs=(); local IFS_=$IFS; IFS=','; for f in $files; do fargs+=(-f "$f"); done; IFS=$IFS_
      (cd "$wd" && "${DC[@]}" "${fargs[@]}" -p "$proj" up -d --no-deps "$svc") \
        && echo "OK     recreated $proj/$svc on new image" || echo "FAIL   compose up for $proj/$svc"
      sleep 5; docker ps --filter "name=$c" --format '{{.Names}} {{.Status}}'
    else
      echo "PULLED newer $img. Standalone container — recreate by hand with its original flags:"
      echo "       docker inspect $c  (note ports/volumes/env)  →  docker stop $c && docker rename $c ${c}-old && docker run … $img"
    fi
  done < <(docker ps --format '{{.Names}} {{.Image}} {{.ID}}' | while read -r n i _; do echo "$n $i $(docker inspect -f '{{.Image}}' "$n")"; done)
  $APPLY && { echo; echo "old images can be removed later with: docker image prune"; }
  $APPLY || printf '\nDRY RUN — nothing changed. Re-run with --apply.\n'
}

main < /dev/null
