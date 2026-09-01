#!/usr/bin/env bash
# ============================================================================================
# sandbox/confine-run.sh — provider-agnostic outside-in confinement launcher (Layer C/D).
#
# Wraps ANY agentic provider/interface command — `goose run …`, a `claude`/`codex`/`copilot` CLI, a
# python agent, an MCP server — in an OS-enforced boundary. It confines a *process*, not a specific
# harness, and OS-dispatches:
#
#   * Linux  → bwrap read-only-root + (netns) + NoNewPrivs   (ENFORCEMENT-VERIFIED by a live-kernel bwrap escape/deny test)
#   * macOS  → sandbox-exec with a path-agnostic Seatbelt profile  (INERT/`--check` here;
#              ENFORCEMENT-UNVERIFIED off a macOS host — no green claim without an on-mac deny test)
#   * other  → FAIL CLOSED (never runs a command while labeled "confined")
#
# PROVENANCE / SOURCE OF TRUTH (operator directive 2026-08-31): this launcher is the FRAMEWORK-NEUTRAL
# reference implementation that agentteams is to EMIT (Linux "works like Seatbelt": an emitted OS-
# confinement boundary, analogous to the macOS `sandbox-exec -f profile <argv>` path). The emitter lives
# in agentteams (`agentteams/frameworks/_linux_sandbox_emit.py`), which emits it verbatim to a
# consuming project's repo-root `sandbox/confine-run.sh`. agentteams is the single source of truth:
# a consuming project keeps its copy BYTE-IDENTICAL to the emitted artifact (guard drift with a
# sha256 pin), and never forks it. It confines ANY provider (goose/claude/codex/copilot) equally —
# NO harness is preferred here; any harness preference belongs in the consuming project, never in
# this launcher.
#
# Policy (POLA / fail-closed): read-only root; ONLY --scratch writable; /tmp scratch; credential dirs
# (~/.ssh ~/.aws ~/.gnupg ~/.kube ~/.config/gcloud ~/.azure) + --exclude paths read-excluded; egress
# deny(default)/proxy(root+netns, OOB)/host(fs-confined only). --writable adds a rw path; --setenv
# passes VAR=VAL into the guest. This closes NOTHING (T6 + host-as-TCB bounded, never closed);
# seccomp/Landlock is a further layer not yet added.
#
# Usage:
#   sandbox/confine-run.sh --scratch DIR [--egress deny|proxy|host] [--proxy ADDR:PORT]
#          [--netns NAME] [--exclude PATH]... [--writable PATH]... [--setenv VAR=VAL]... [--check]
#          -- CMD [ARGS...]
# ============================================================================================
set -uo pipefail

SCRATCH=""; EGRESS="deny"; PROXY_ADDR="127.0.0.1"; PROXY_PORT="8443"; NETNS="baseagent-egress"
CHECK=0; EXCLUDES=(); WRITABLES=(); SETENVS=(); CMD=()

die(){ echo "confine-run: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --scratch) SCRATCH="${2:-}"; shift 2 ;;
    --egress)  EGRESS="${2:-}"; shift 2 ;;
    --proxy)   IFS=: read -r PROXY_ADDR PROXY_PORT <<<"${2:-}"; shift 2 ;;
    --netns)   NETNS="${2:-}"; shift 2 ;;
    --exclude) EXCLUDES+=("${2:-}"); shift 2 ;;
    --writable) WRITABLES+=("${2:-}"); shift 2 ;;
    --setenv)  SETENVS+=("${2:-}"); shift 2 ;;
    --check)   CHECK=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; CMD=("$@"); break ;;
    *) die "unknown arg: $1 (did you forget '--' before the command?)" ;;
  esac
done

# -- generic validation (OS-independent) ------------------------------------------------------
[ -n "$SCRATCH" ] || die "--scratch DIR is required (the only writable path inside the sandbox)"
[ -d "$SCRATCH" ] || die "--scratch '$SCRATCH' does not exist or is not a directory (fail-closed)"
[ "${#CMD[@]}" -gt 0 ] || die "no command given — put it after '--'"
case "$EGRESS" in deny|proxy|host) ;; *) die "--egress must be deny|proxy|host (got '$EGRESS')" ;; esac
SCRATCH="$(cd "$SCRATCH" && pwd)"

# credential + caller read-excludes: only EXISTING paths (masking a missing path fails fail-shut: D-3).
MASK=( "$HOME/.ssh" "$HOME/.aws" "$HOME/.gnupg" "$HOME/.kube" "$HOME/.config/gcloud" "$HOME/.azure" )
MASK+=( "${EXCLUDES[@]}" )
MASKED=()
for p in "${MASK[@]}"; do [ -n "$p" ] && [ -e "$p" ] && MASKED+=( "$p" ); done
# ensure writable paths exist so the mount source is real
for w in "${WRITABLES[@]}"; do [ -n "$w" ] && { mkdir -p "$w" 2>/dev/null || die "cannot create --writable '$w'"; }; done

OS="$(uname -s)"

# ============================================================================================
build_linux() {   # -> RUN[] using bwrap
  command -v bwrap >/dev/null 2>&1 || die "bwrap (bubblewrap) not found — install: sudo apt-get install -y bubblewrap"
  local BW=( bwrap --ro-bind / / --tmpfs /tmp --dev /dev --proc /proc
                   --bind "$SCRATCH" "$SCRATCH" --chdir "$SCRATCH"
                   --die-with-parent --new-session
                   --unshare-user --unshare-ipc --unshare-pid --unshare-uts --unshare-cgroup )
  local w; for w in "${WRITABLES[@]}"; do [ -n "$w" ] && BW+=( --bind "$w" "$w" ); done
  local kv; for kv in "${SETENVS[@]}"; do [ -n "$kv" ] && { case "$kv" in *=*) BW+=( --setenv "${kv%%=*}" "${kv#*=}" ) ;; *) die "--setenv expects VAR=VAL (got '$kv')" ;; esac; }; done
  local m; for m in "${MASKED[@]}"; do BW+=( --tmpfs "$m" ); done
  case "$EGRESS" in
    deny) BW+=( --unshare-net ); RUN=( "${BW[@]}" -- "${CMD[@]}" ) ;;
    host) echo "confine-run: ⚠ --egress host — network SHARED with host; egress NOT confined (fs/read/NNP still apply)." >&2
          RUN=( "${BW[@]}" -- "${CMD[@]}" ) ;;
    proxy) command -v ip >/dev/null 2>&1 || die "--egress proxy needs iproute2 (ip)"
           ip netns list 2>/dev/null | grep -qw "$NETNS" || die "--egress proxy requires netns '$NETNS' to already exist (apply as root via egress-netns-wiring.sh --apply, then re-run). [OPERATOR-OOB]"
           RUN=( ip netns exec "$NETNS" "${BW[@]}" -- "${CMD[@]}" ) ;;
  esac
}

# ============================================================================================
build_macos() {   # -> RUN[] using sandbox-exec + a generated path-agnostic Seatbelt profile
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not found (macOS only)"
  local sb="$SCRATCH/.confine.sandbox.sb"
  {
    echo '(version 1)'
    echo '(allow default)'
    echo '(deny file-write*)'
    echo "(allow file-write* (subpath \"$SCRATCH\"))"
    echo '(allow file-write* (subpath "/private/tmp") (subpath "/private/var/folders") (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))'
    local w; for w in "${WRITABLES[@]}"; do [ -n "$w" ] && echo "(allow file-write* (subpath \"$(cd "$w" && pwd)\"))"; done
    local m; for m in "${MASKED[@]}"; do echo "(deny file-read* (subpath \"$m\"))"; done
    case "$EGRESS" in
      deny) echo '(deny network*)' ;;
      host) : ;;  # network stays allowed by (allow default)
      proxy) echo '(deny network*)'; echo "(allow network* (remote ip \"$PROXY_ADDR:$PROXY_PORT\"))"
             echo "; NOTE: Seatbelt host/port egress filtering is best-effort; the authoritative sole-proxy boundary is OP1 (OOB)." ;;
    esac
  } > "$sb"
  local PREFIX=()
  local kv; for kv in "${SETENVS[@]}"; do [ -n "$kv" ] && { case "$kv" in *=*) PREFIX+=( "$kv" ) ;; *) die "--setenv expects VAR=VAL (got '$kv')" ;; esac; }; done
  if [ "${#PREFIX[@]}" -gt 0 ]; then
    RUN=( env "${PREFIX[@]}" sandbox-exec -f "$sb" "${CMD[@]}" )
  else
    RUN=( sandbox-exec -f "$sb" "${CMD[@]}" )
  fi
  [ "$EGRESS" = host ] && echo "confine-run: ⚠ --egress host — network NOT confined (fs/read still apply)." >&2
  echo "confine-run: ⚠ macOS Seatbelt path is ENFORCEMENT-UNVERIFIED until an on-mac deny test passes." >&2
}

case "$OS" in
  Linux)  build_linux ;;
  Darwin) build_macos ;;
  *) die "unsupported OS '$OS' — confinement is Linux (bwrap) or macOS (sandbox-exec) only. FAIL CLOSED." ;;
esac

if [ "$CHECK" -eq 1 ]; then
  echo "== confine-run --check (inert; nothing runs) =="
  echo "  os                : $OS"
  echo "  scratch (writable): $SCRATCH"
  echo "  egress mode       : $EGRESS$( [ "$EGRESS" = proxy ] && echo "  (netns=$NETNS, proxy=$PROXY_ADDR:$PROXY_PORT)" )"
  echo "  read-excluded     : ${MASKED[*]:-<none present>}"
  echo "  command           : ${CMD[*]}"
  echo "  effective         : ${RUN[*]}"
  exit 0
fi

exec "${RUN[@]}"
