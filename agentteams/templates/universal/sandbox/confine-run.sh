#!/usr/bin/env bash
# ============================================================================================
# sandbox/confine-run.sh - provider-agnostic outside-in confinement launcher (Layer C/D).
#
# Wraps ANY agentic provider/interface command - `goose run ...`, a `claude`/`codex`/`copilot` CLI, a
# python agent, an MCP server - in an OS-enforced boundary. It confines a *process*, not a specific
# harness, and OS-dispatches:
#
#   * Linux  -> bwrap read-only-root + (netns) + NoNewPrivs   (ENFORCEMENT-VERIFIED by a live-kernel bwrap escape/deny test)
#   * macOS  -> sandbox-exec with a path-agnostic Seatbelt profile  (INERT/`--check` here;
#              ENFORCEMENT-UNVERIFIED off a macOS host - no green claim without an on-mac deny test)
#   * other  -> FAIL CLOSED (never runs a command while labeled "confined")
#
# PROVENANCE / SOURCE OF TRUTH (operator directive 2026-08-31): this launcher is the FRAMEWORK-NEUTRAL
# reference implementation that agentteams is to EMIT (Linux "works like Seatbelt": an emitted OS-
# confinement boundary, analogous to the macOS `sandbox-exec -f profile <argv>` path). The emitter lives
# in agentteams (`agentteams/frameworks/_linux_sandbox_emit.py`), which emits it verbatim to a
# consuming project's repo-root `sandbox/confine-run.sh`. agentteams is the single source of truth:
# a consuming project keeps its copy BYTE-IDENTICAL to the emitted artifact (guard drift with a
# sha256 pin), and never forks it. It confines ANY provider (goose/claude/codex/copilot) equally -
# NO harness is preferred here; any harness preference belongs in the consuming project, never in
# this launcher.  NOTE: this file is intentionally ASCII-only so the sha256 parity pin cannot drift
# through a UTF-8 re-encode.
#
# Policy (POLA / fail-closed): read-only root; ONLY --scratch writable; /tmp scratch; credential dirs
# (~/.ssh ~/.aws ~/.gnupg ~/.kube ~/.config/gcloud ~/.azure) + --exclude paths read-excluded; egress
# deny(default)/proxy(root+netns, OOB)/host(fs-confined only). --writable adds a rw path; --setenv
# passes VAR=VAL into the guest. This closes NOTHING (T6 + host-as-TCB bounded, never closed);
# seccomp/Landlock is a further layer not yet added.
#
# Usage:
#   sandbox/confine-run.sh --scratch DIR [--egress deny|proxy|host] [--proxy ADDR:PORT]
#          [--netns NAME] [--exclude PATH]... [--writable PATH]... [--setenv VAR=VAL]...
#          [--cpu-max SEC] [--nproc-max N] [--mem-max MiB] [--check]
#          -- CMD [ARGS...]
#
# macOS AUGMENTATION (2026-W36) - added ONLY to the macOS (Darwin) branch. TWO DISTINCT mechanisms;
# do NOT conflate them (only group (i) is an actual Seatbelt/sandbox-exec feature):
#   (i)  SBPL / Seatbelt profile additions (the only real sandbox-exec expressions here):
#          - setuid-exec denylist:  (deny process-exec* (literal ...))   [best-effort, NOT exhaustive]
#          - egress deny / loopback-proxy allow rules
#   (ii) POSIX rlimits applied via `ulimit` BEFORE exec (KERNEL limits, NOT Seatbelt/SBPL features):
#          --cpu-max SEC  -> RLIMIT_CPU ;   --nproc-max N -> RLIMIT_NPROC
#   (iii) fail-honest, interface-only (enforces NOTHING on macOS):  --mem-max
# The Linux (bwrap) branch is unchanged; the three cap flags are no-ops there (Linux reaches CPU/PID/
# mem caps via cgroups on its OOB path). LINUX DELTA (for the parity test): these three flags were
# previously `unknown arg` -> exit 2; they now no-op-with-note -> exit 0.
#   --cpu-max SEC   (ii) RLIMIT_CPU: a per-PROCESS cpu-second cap - EACH descendant gets its OWN
#                   counter, so this is DoS-bounding, not a process-tree/aggregate quota. Aggregate/
#                   tree bounding needs --nproc-max under a dedicated uid, or cgroups on Linux. The
#                   process is terminated (SIGXCPU at the soft limit / SIGKILL at the hard limit, set
#                   equal) after SEC cpu-seconds. NOT a throttle or a fair-share quota.
#   --nproc-max N   (ii) RLIMIT_NPROC: bounds a *tenant* ONLY when the whole launcher is ALREADY
#                   running as a dedicated, operator-provisioned uid. On a shared uid RLIMIT_NPROC is
#                   per-UID and this flag is just a self-DoS knob, not isolation. The launcher does NOT
#                   drop uid (that would make it root-requiring - a rejected escalation surface). See
#                   dedicated-uid-provisioning.example.sh (inert, OOB).
#   --mem-max MiB   (iii) accepted for interface parity with Linux MemoryMax, but on macOS it does NOT
#                   cap memory. RLIMIT_AS/DATA/RSS are broken on arm64 and `taskpolicy -m` fires only
#                   under system memory pressure. The launcher prints a LOUD warning and PROCEEDS
#                   UNCAPPED. A hard memory cap requires a VM / container / Linux host (Layer B).
#
# DNS-THROUGH-PROXY CONTRACT (--egress proxy on macOS): SBPL has no hostname filter and its network
# address filter accepts ONLY host "*" or "localhost". `(deny network*)` blocks UDP/53 so the guest
# CANNOT self-resolve; only the loopback proxy port is opened. Therefore --proxy MUST be a loopback
# IP:PORT and the proxy MUST perform all name resolution on the guest's behalf. A remote proxy IP
# cannot be expressed in SBPL and FAILS CLOSED. Authoritative sole-proxy boundary is still OP1 (OOB).
#
# TIER C OMISSION (operator decision 2026-W36): NO syscall filtering is emitted. SBPL `syscall-unix`
# is undocumented and OS-/arch-version-gated; a curated set pins to one OS build. seccomp-grade policy
# = Linux host / Layer B. This launcher never emits `(deny syscall-*)` and never `(with no-sandbox)`.
# ============================================================================================
set -uo pipefail

SCRATCH=""; EGRESS="deny"; PROXY_ADDR="127.0.0.1"; PROXY_PORT="8443"; NETNS="agentteams-egress"
CHECK=0; EXCLUDES=(); WRITABLES=(); SETENVS=(); CMD=()
# macOS-augmentation resource caps (empty = unset; validated numeric below). No-op on Linux.
CPU_MAX=""; NPROC_MAX=""; MEM_MAX=""

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
    --cpu-max)  CPU_MAX="${2:-}";  shift 2 ;;   # macOS: RLIMIT_CPU (SEC cpu-seconds); no-op on Linux
    --nproc-max) NPROC_MAX="${2:-}"; shift 2 ;; # macOS: RLIMIT_NPROC (dedicated-uid only); no-op on Linux
    --mem-max)  MEM_MAX="${2:-}";  shift 2 ;;   # macOS: interface-only, UNCAPPED; no-op on Linux
    --check)   CHECK=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; CMD=("$@"); break ;;
    *) die "unknown arg: $1 (did you forget '--' before the command?)" ;;
  esac
done

# -- generic validation (OS-independent) ------------------------------------------------------
[ -n "$SCRATCH" ] || die "--scratch DIR is required (the only writable path inside the sandbox)"
[ -d "$SCRATCH" ] || die "--scratch '$SCRATCH' does not exist or is not a directory (fail-closed)"
[ "${#CMD[@]}" -gt 0 ] || die "no command given - put it after '--'"
case "$EGRESS" in deny|proxy|host) ;; *) die "--egress must be deny|proxy|host (got '$EGRESS')" ;; esac
# resource-cap args must be positive integers (also closes any injection into the ulimit sub-shell).
is_posint(){ case "$1" in ''|*[!0-9]*) return 1 ;; 0) return 1 ;; *) return 0 ;; esac; }
[ -n "$CPU_MAX" ]   && { is_posint "$CPU_MAX"   || die "--cpu-max expects a positive integer (cpu-seconds), got '$CPU_MAX'"; }
[ -n "$NPROC_MAX" ] && { is_posint "$NPROC_MAX" || die "--nproc-max expects a positive integer, got '$NPROC_MAX'"; }
[ -n "$MEM_MAX" ]   && { is_posint "$MEM_MAX"   || die "--mem-max expects a positive integer (MiB), got '$MEM_MAX'"; }
# proxy port must be a positive integer in range (interpolated verbatim into the SBPL profile).
if [ "$EGRESS" = proxy ]; then
  is_posint "$PROXY_PORT" && [ "$PROXY_PORT" -le 65535 ] || die "--proxy PORT must be an integer in 1..65535 (got '$PROXY_PORT')"
fi
SCRATCH="$(cd "$SCRATCH" && pwd)"

# credential + caller read-excludes: only EXISTING paths (masking a missing path fails fail-shut: D-3).
MASK=( "$HOME/.ssh" "$HOME/.aws" "$HOME/.gnupg" "$HOME/.kube" "$HOME/.config/gcloud" "$HOME/.azure" )
MASK+=( ${EXCLUDES[@]+"${EXCLUDES[@]}"} )
MASKED=()
for p in "${MASK[@]}"; do [ -n "$p" ] && [ -e "$p" ] && MASKED+=( "$p" ); done
# ensure writable paths exist so the mount source is real
for w in ${WRITABLES[@]+"${WRITABLES[@]}"}; do [ -n "$w" ] && { mkdir -p "$w" 2>/dev/null || die "cannot create --writable '$w'"; }; done

OS="$(uname -s)"

# ============================================================================================
build_linux() {   # -> RUN[] using bwrap
  command -v bwrap >/dev/null 2>&1 || die "bwrap (bubblewrap) not found - install: sudo apt-get install -y bubblewrap"
  # macOS-augmentation resource-cap flags are NO-OPS on Linux (degrade safely): Linux reaches CPU/
  # PID/memory caps through cgroups on its OOB netns/systemd path, not through these flags.
  # LINUX DELTA (parity test): these flags used to be an `unknown arg` hard-fail (exit 2); now a no-op.
  if [ -n "$CPU_MAX$NPROC_MAX$MEM_MAX" ]; then
    echo "confine-run: note - --cpu-max/--nproc-max/--mem-max are macOS-branch flags; ignored on Linux (use cgroups OOB)." >&2
  fi
  local BW=( bwrap --ro-bind / / --tmpfs /tmp --dev /dev --proc /proc
                   --bind "$SCRATCH" "$SCRATCH" --chdir "$SCRATCH"
                   --die-with-parent --new-session
                   --unshare-user --unshare-ipc --unshare-pid --unshare-uts --unshare-cgroup )
  local w; for w in ${WRITABLES[@]+"${WRITABLES[@]}"}; do [ -n "$w" ] && BW+=( --bind "$w" "$w" ); done
  local kv; for kv in ${SETENVS[@]+"${SETENVS[@]}"}; do [ -n "$kv" ] && { case "$kv" in *=*) BW+=( --setenv "${kv%%=*}" "${kv#*=}" ) ;; *) die "--setenv expects VAR=VAL (got '$kv')" ;; esac; }; done
  local m; for m in ${MASKED[@]+"${MASKED[@]}"}; do BW+=( --tmpfs "$m" ); done
  case "$EGRESS" in
    deny) BW+=( --unshare-net ); RUN=( "${BW[@]}" -- "${CMD[@]}" ) ;;
    host) echo "confine-run: WARNING --egress host - network SHARED with host; egress NOT confined (fs/read/NNP still apply)." >&2
          RUN=( "${BW[@]}" -- "${CMD[@]}" ) ;;
    proxy) command -v ip >/dev/null 2>&1 || die "--egress proxy needs iproute2 (ip)"
           ip netns list 2>/dev/null | grep -qw "$NETNS" || die "--egress proxy requires netns '$NETNS' to already exist (apply as root via egress-netns-wiring.sh --apply, then re-run). [OPERATOR-OOB]"
           RUN=( ip netns exec "$NETNS" "${BW[@]}" -- "${CMD[@]}" ) ;;
  esac
}

# ============================================================================================
# Reject any path that would be interpolated verbatim into the generated SBPL profile if it contains
# a double-quote, a backslash, or a newline - any of these can break out of the `(subpath "...")`
# string and inject arbitrary SBPL directives, widening the profile (SECURITY C1). A backslash is the
# SBPL/string escape char and can escape the closing quote even when no literal double-quote is
# present (agentteams @security 2026-W36 - closed a residual the quote/newline check missed). Applied
# to $SCRATCH, every resolved --writable path, and every masked credential/--exclude path. macOS-only
# (SBPL is macOS-only), so the Linux branch keeps its exact prior behavior.
reject_sbpl_meta(){
  case "$1" in
    *\\*)    die "path contains a backslash, which could escape the SBPL string quote (fail-closed): $1" ;;
    *'"'*)   die "path contains a double-quote, which could inject SBPL directives (fail-closed): $1" ;;
    *$'\n'*) die "path contains a newline, which could inject SBPL directives (fail-closed): $1" ;;
  esac
}

# ============================================================================================
build_macos() {   # -> RUN[] using sandbox-exec + a generated path-agnostic Seatbelt profile
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not found (macOS only)"
  local sb="$SCRATCH/.confine.sandbox.sb"
  # -- proxy egress: SBPL network address filters accept ONLY host "*" or "localhost" (verified on
  # 26.5.2 arm64: `(remote ip "<any-real-IP>:port")` fails at PARSE -> "host must be * or localhost").
  # So macOS can pin sole-proxy egress ONLY to a loopback proxy. A remote proxy IP cannot be expressed;
  # we FAIL-HONEST (die) rather than silently widen to `*:port` (which would NOT be sole-proxy).
  # (::1 is intentionally NOT accepted: `IFS=: read` splits it away, and IPv6 loopback is not needed.)
  if [ "$EGRESS" = proxy ]; then
    case "$PROXY_ADDR" in
      127.0.0.1|localhost) : ;;   # loopback proxy - expressible as (remote ip "localhost:PORT")
      *) die "--egress proxy: macOS Seatbelt cannot pin egress to a remote proxy IP ('$PROXY_ADDR'); \
SBPL only allows host '*' or 'localhost'. Run the proxy on loopback (127.0.0.1 - e.g. an ssh -L / \
socat forward) OR use the OOB dedicated-uid + PF-per-tenant path. FAIL CLOSED." ;;
    esac
  fi
  # SECURITY C1: validate every path that gets interpolated into the profile BEFORE it is written.
  reject_sbpl_meta "$SCRATCH"
  local w; for w in ${WRITABLES[@]+"${WRITABLES[@]}"}; do [ -n "$w" ] && reject_sbpl_meta "$(cd "$w" && pwd)"; done
  local m; for m in ${MASKED[@]+"${MASKED[@]}"}; do reject_sbpl_meta "$m"; done
  {
    echo '(version 1)'
    echo '(allow default)'
    echo '(deny file-write*)'
    echo "(allow file-write* (subpath \"$SCRATCH\"))"
    echo '(allow file-write* (subpath "/private/tmp") (subpath "/private/var/folders") (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))'
    for w in ${WRITABLES[@]+"${WRITABLES[@]}"}; do [ -n "$w" ] && echo "(allow file-write* (subpath \"$(cd "$w" && pwd)\"))"; done
    for m in ${MASKED[@]+"${MASKED[@]}"}; do echo "(deny file-read* (subpath \"$m\"))"; done
    # -- (i) SBPL setuid/setgid-exec restriction (compensating hardening, NOT a no-new-privs guarantee) --
    # SBPL cannot express the setuid BIT itself, so this is a BEST-EFFORT, NOT-EXHAUSTIVE denylist of
    # the known macOS system setuid-/setgid-root escalation binaries, denied via
    # `(deny process-exec* (literal ...))`.
    # OOB RECONCILIATION: enumerate the box's actual setuid/setgid set and reconcile this list with:
    #     find / -perm -4000 -o -perm -2000  2>/dev/null
    # HONEST CEILING: (1) this is NOT `no-new-privs` - it only blocks these specific exec targets;
    # (2) the list is best-effort and NOT exhaustive - a setuid binary not listed here is not blocked;
    # (3) literal-path matching may be bypassable by path aliasing (symlink/hardlink) - the deny test
    #     (mac-escape-tests.sh gates H2) probes this; if a bypass is found the label downgrades to
    #     "blocks only direct literal-path invocation";
    # (4) it does NOT cover an arbitrary attacker-supplied setuid file - but a file copied into the
    #     writable scratch cannot become setuid-ROOT unprivileged (no chown-to-root), so the real
    #     escalation vector is exec of the pre-existing system setuid-root binaries below;
    # (5) we NEVER emit `(with no-sandbox)` (rejected by SBPL for deny anyway, and an escape hatch by
    #     design). Add paths here rather than widening to a `(with no-sandbox)` allow.
    local suid; for suid in \
        /usr/bin/sudo /usr/bin/su /usr/bin/login /usr/bin/newgrp /usr/bin/passwd \
        /usr/bin/crontab /usr/bin/at /usr/bin/atq /usr/bin/atrm /usr/bin/batch \
        /usr/bin/quota /usr/libexec/authopen /usr/libexec/security_authtrampoline \
        /usr/sbin/traceroute /usr/sbin/traceroute6 ; do
      echo "(deny process-exec* (literal \"$suid\"))"
    done
    case "$EGRESS" in
      deny) echo '(deny network*)' ;;   # also blocks UDP/53 -> guest cannot self-resolve DNS
      host) : ;;  # network stays allowed by (allow default)
      proxy) # DNS-THROUGH-PROXY CONTRACT: `(deny network*)` blocks UDP/53 so the guest cannot
             # self-resolve; ONLY the loopback proxy port is opened (host pinned to "localhost" - the
             # only non-"*" host SBPL accepts). The proxy MUST run on loopback and MUST do all name
             # resolution. Authoritative sole-proxy boundary is OP1 (OOB).
             echo '(deny network*)'; echo "(allow network* (remote ip \"localhost:$PROXY_PORT\"))"
             echo "; NOTE: SBPL host filter is limited to '*'/'localhost'; sole-proxy egress is pinned to"
             echo ";       the loopback proxy port only, and the proxy performs all DNS. Boundary = OP1 (OOB)." ;;
    esac
  } > "$sb"
  local PREFIX=()
  local kv; for kv in ${SETENVS[@]+"${SETENVS[@]}"}; do [ -n "$kv" ] && { case "$kv" in *=*) PREFIX+=( "$kv" ) ;; *) die "--setenv expects VAR=VAL (got '$kv')" ;; esac; }; done
  # inner command = (optional env prefix) + sandbox-exec on the generated profile
  local INNER=()
  if [ "${#PREFIX[@]}" -gt 0 ]; then INNER=( env "${PREFIX[@]}" sandbox-exec -f "$sb" "${CMD[@]}" )
  else INNER=( sandbox-exec -f "$sb" "${CMD[@]}" ); fi
  # -- (ii) POSIX rlimits via ulimit (KERNEL, not Seatbelt), applied to the launcher process and
  # INHERITED by the guest across exec (an unprivileged guest cannot raise them). Wrapped in a tiny
  # sub-shell so the effective RUN[] is honest/visible in --check and self-contained.
  local ULIM=""
  if [ -n "$CPU_MAX" ]; then
    # RLIMIT_CPU: per-PROCESS cpu-second cap (each descendant gets its own counter). SIGXCPU at the
    # soft limit (default action: terminate); SIGKILL at the hard limit. `ulimit -t` sets both equal.
    ULIM+="ulimit -t $CPU_MAX; "
  fi
  if [ -n "$NPROC_MAX" ]; then
    # RLIMIT_NPROC is PER-UID. This isolates a tenant ONLY when confine-run.sh is ALREADY running as a
    # dedicated, operator-provisioned uid (see dedicated-uid-provisioning.example.sh). On a shared
    # login uid it counts ALL the user's processes -> a self-DoS knob, NOT isolation. The launcher
    # deliberately does NOT drop uid (no sudo/launchctl - that would make it root-requiring and an
    # argv-fed escalation surface). Provisioning the uid is OOB.
    echo "confine-run: WARNING --nproc-max only isolates a tenant under a DEDICATED uid (operator-provisioned); on a shared uid it is a self-DoS knob, not isolation." >&2
    ULIM+="ulimit -u $NPROC_MAX; "
  fi
  if [ -n "$ULIM" ]; then RUN=( bash -c "${ULIM}exec \"\$@\"" _ "${INNER[@]}" ); else RUN=( "${INNER[@]}" ); fi
  # -- (iii) memory is fail-honest. Accept --mem-max for interface parity, but do NOT claim a cap.
  if [ -n "$MEM_MAX" ]; then
    echo "confine-run: WARNING!! MEMORY UNCAPPED on macOS - --mem-max ${MEM_MAX}MiB is NOT enforced." >&2
    echo "confine-run:    (RLIMIT_AS/DATA/RSS broken on arm64; taskpolicy fires only under pressure.)" >&2
    echo "confine-run:    A hard memory cap requires a VM / container / Linux host (Layer B). Proceeding UNCAPPED." >&2
  fi
  [ "$EGRESS" = host ] && echo "confine-run: WARNING --egress host - network NOT confined (fs/read still apply)." >&2
  echo "confine-run: WARNING macOS Seatbelt path is ENFORCEMENT-UNVERIFIED until an on-mac deny test passes." >&2
}

case "$OS" in
  Linux)  build_linux ;;
  Darwin) build_macos ;;
  *) die "unsupported OS '$OS' - confinement is Linux (bwrap) or macOS (sandbox-exec) only. FAIL CLOSED." ;;
esac

if [ "$CHECK" -eq 1 ]; then
  echo "== confine-run --check (inert; nothing runs) =="
  echo "  os                : $OS"
  echo "  scratch (writable): $SCRATCH"
  if [ "$EGRESS" = proxy ] && [ "$OS" = Darwin ]; then
    # macOS emits the loopback form regardless of PROXY_ADDR (validated to loopback above).
    echo "  egress mode       : proxy  (macOS emits: (allow network* (remote ip \"localhost:$PROXY_PORT\")))"
  elif [ "$EGRESS" = proxy ]; then
    echo "  egress mode       : proxy  (netns=$NETNS, proxy=$PROXY_ADDR:$PROXY_PORT)"
  else
    echo "  egress mode       : $EGRESS"
  fi
  echo "  read-excluded     : ${MASKED[*]:-<none present>}"
  echo "  cpu-max (RLIMIT)  : ${CPU_MAX:-<none>}$( [ -n "$CPU_MAX" ] && echo " cpu-sec (POSIX RLIMIT_CPU, per-process, kernel-enforced, DoS-bound)" )"
  echo "  nproc-max (RLIMIT): ${NPROC_MAX:-<none>}$( [ -n "$NPROC_MAX" ] && echo " (POSIX RLIMIT_NPROC, per-uid; isolates only under a dedicated uid)" )"
  echo "  mem-max           : ${MEM_MAX:-<none>}$( [ -n "$MEM_MAX" ] && echo " MiB requested - UNCAPPED on macOS (interface-only, fail-honest)" )"
  echo "  command           : ${CMD[*]}"
  echo "  effective         : ${RUN[*]}"
  exit 0
fi

exec "${RUN[@]}"
