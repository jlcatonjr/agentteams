#!/usr/bin/env bash
# Unattended remediation after each daily vulnscan run (runtime: goose + OpenRouter + GLM 5.3).
# Walkthrough: docs_src/vulnscan-automation-walkthrough.md, "Unattended remediation runner".
#
#   bash scripts/vulnscan-remediation/vulnscan-remediate-auto.sh --install   # once: schedule it (launchd, every 30 min)
#   bash scripts/vulnscan-remediation/vulnscan-remediate-auto.sh             # run now (does nothing if done today)
#   bash scripts/vulnscan-remediation/vulnscan-remediate-auto.sh --uninstall
#
# Each run, once today's daily run has finished (scan and, when on, agent phase) and remediation has not run for it yet:
#   1. preflight: goose + route proxy up, grants valid (warns 7 days before a grant expires: renewing needs your
#      key, the one recurring manual step), no open escalation or HALT (vulnscan halts), and
#      vulnscan-ensure-docker.sh READY (it starts Docker Desktop if needed); when Docker, goose or the route proxy
#      is not usable the remediation steps are skipped for this attempt instead of being refused one by one;
#   2. vulnscan-target-agents.sh --yes: goose team + manager roster for any repo that newly has findings;
#   3. the site-local vulnscan-operator-actions.sh --yes 12 13 9 16 15 (not in this repo: it holds site-specific
#      steps; VULNSCAN_OPERATOR_ACTIONS, default $INSTALL_DIR/vulnscan-operator-actions.sh): keep the runtime
#      standard, resume pending handoffs, hand off new fixes (stage-fix → packet → signed directive → unattended
#      confined goose orchestrator → verdict → executor open-pr on approve-staged), retry open-pr for fixes
#      approved earlier, and run server dry runs;
#   4. `python -m agentteams.vulnscan remediation-report` reads this attempt's ledger rows (not log text) and
#      writes <run>/remediation.md: every refused/failed row is under "Needs you", classified infra (retryable)
#      only by the executor's anchored prefix or an infra.* reason_code; an unreadable ledger counts as infra;
#   5. remediation.done when no infra refusal happened; otherwise remediation.blocked (attempt n) and the next
#      30-minute tick retries, up to MAX_ATTEMPTS or until GIVE_UP_HOUR local, then done + a final notification.
# One run at a time: remediate.flock, held by a small holder process (the pipx python) for exactly as long as
# this script's pid lives, so a killed run never leaves a stale lock and its children never inherit it.
# Never done unattended: grant signing, destructive deletes, server --apply, merging, pushing a team's own branch.
#
# --install copies the tracked scripts to ~/.config/agentteams/vulnscan-remediation/ (so the job does not depend
# on this checkout or its branch), writes ~/Library/LaunchAgents/io.agentteams.vulnscan-remediate.plist and loads
# it. Installing is your standing authorization for auto mode; it is recorded in the clearance log. Deploy the
# package first (vulnscan-deploy.sh): the installed script calls the installed package's remediation-report.

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
export VULNSCAN_AUTO=1
. "$SRC/vulnscan-handoff-lib.sh"
LABEL="io.agentteams.vulnscan-remediate"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INSTALL_DIR="$CFG_DIR/vulnscan-remediation"
# The tracked scripts --install copies (vulnscan-operator-actions.sh is site-local: never copied, never removed).
FILES=(vulnscan-handoff-lib.sh vulnscan-target-agents.sh vulnscan-remediate-auto.sh
       vulnscan-ensure-docker.sh vulnscan-daily-wrapped.sh)
OPERATOR_ACTIONS="${VULNSCAN_OPERATOR_ACTIONS:-$INSTALL_DIR/vulnscan-operator-actions.sh}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
GIVE_UP_HOUR="${GIVE_UP_HOUR:-17}"

xml_esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }   # bash 3.2 and 5 alike

install_job() {
  local interval="${1:-1800}" f path bak
  [[ "$interval" =~ ^[0-9]+$ && "$interval" -ge 60 ]] || { bad "interval must be whole seconds, at least 60: $interval"; exit 2; }
  mkdir -p "$INSTALL_DIR" "$STATE_DIR" "$(dirname "$PLIST")"
  [[ -f "$OPERATOR_ACTIONS" ]] || warn "no $OPERATOR_ACTIONS: the handoff steps are reported as a problem until it exists"
  if [[ ! -f "$REMEDIATION_CONF" ]]; then
    printf '%s\n' "# vulnscan remediation site settings (KEY=VALUE; read, never sourced). See vulnscan-handoff-lib.sh." \
      "# VULNSCAN_REPOS_ROOT=~/githubrepositories" "# VULNSCAN_GITHUB_ORG=your-org" "# MANAGER_TEAM=ops-team" \
      > "$REMEDIATION_CONF"
    warn "wrote $REMEDIATION_CONF commented out: set VULNSCAN_GITHUB_ORG there, or no repo is handed off"
  fi
  # Refuse to schedule a job that would fail every attempt: no org means no repo is handed off, and the runner
  # needs the installed package's remediation-report (deploy the package first).
  [[ -n "$REPO_ORG" ]] || { bad "VULNSCAN_GITHUB_ORG is not set in $REMEDIATION_CONF: not installing"; exit 2; }
  [[ -d "$REPOS_ROOT" ]] || { bad "VULNSCAN_REPOS_ROOT $REPOS_ROOT does not exist: not installing"; exit 2; }
  "${VS[@]}" remediation-report --help >/dev/null 2>&1 ||
    { bad "the installed agentteams has no 'remediation-report' (deploy the package first): not installing"; exit 2; }
  # Only now replace what the loaded job runs (a refused install changes nothing), keeping what it replaces.
  bak="$CFG_DIR/vulnscan-remediation-backups/$(date +%Y%m%dT%H%M%S)"
  for f in "${FILES[@]}"; do [[ -f "$INSTALL_DIR/$f" ]] && { mkdir -p "$bak"; cp -p "$INSTALL_DIR/$f" "$bak/"; }; done
  [[ -f "$PLIST" ]] && { mkdir -p "$bak"; cp -p "$PLIST" "$bak/"; }
  [[ -d "$bak" ]] && ok "backed up the replaced scripts and plist to $bak"
  for f in "${FILES[@]}"; do install -m 0755 "$SRC/$f" "$INSTALL_DIR/$f"; done
  path="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$(dirname "$(command -v node 2>/dev/null || echo /usr/bin/node)"):/usr/bin:/bin:/usr/sbin:/sbin"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$(xml_esc "$LABEL")</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$(xml_esc "$INSTALL_DIR/vulnscan-remediate-auto.sh")</string></array>
  <key>StartInterval</key><integer>$interval</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$(xml_esc "$HOME")</string>
    <key>PATH</key><string>$(xml_esc "$path")</string>
  </dict>
  <key>StandardOutPath</key><string>$(xml_esc "$STATE_DIR/remediate-scheduler.log")</string>
  <key>StandardErrorPath</key><string>$(xml_esc "$STATE_DIR/remediate-scheduler.log")</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  clearance "installed $LABEL: unattended remediation in auto mode every $((interval / 60)) min after each daily scan"
  ok "installed: $PLIST → $INSTALL_DIR/vulnscan-remediate-auto.sh (every $((interval / 60)) min; acts once per scan)"
  echo "  Re-run --install after editing the scripts in this repo so the installed copies match."
}

case "${1:-}" in
  --install) install_job "${2:-1800}"; exit 0 ;;
  --uninstall) launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; rm -f "$PLIST"
               clearance "uninstalled $LABEL"; ok "uninstalled"; exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--install [interval-seconds] | --uninstall]"; exit 2 ;;
esac

# ------------------------------------------------------------------------------------------- run once
LOCK="$STATE_DIR/remediate.flock"
LEGACY_LOCK="$STATE_DIR/remediate.lock"   # the mkdir lock of earlier versions (a directory holding a pid file)
mkdir -p "$STATE_DIR"
# A legacy lock directory: honoured while its recorded pid runs; removed when that owner is gone (or, with no pid
# file, when it is older than 2 h). A live run that is merely slow is never broken into.
if [[ -d "$LEGACY_LOCK" ]]; then
  holder="$(cat "$LEGACY_LOCK/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) a remediation run of an earlier version is active (pid $holder)"; exit 0
  fi
  if [[ -n "$holder" ]] || [[ -n "$(find "$LEGACY_LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]]; then
    echo "$(date -u +%FT%TZ) removing a stale legacy remediate.lock (owner ${holder:-unknown, older than 2 h} is gone)"
    clearance "removed stale $LEGACY_LOCK (owner ${holder:-unknown} gone)"
    rm -f "$LEGACY_LOCK/pid"; rmdir "$LEGACY_LOCK" 2>/dev/null || true
  else
    echo "$(date -u +%FT%TZ) a legacy remediate.lock without a pid is younger than 2 h; not running"; exit 0
  fi
fi
# flock, held by a holder process for as long as this script's pid lives (kqueue exit notification; elsewhere a
# 1 s poll): a killed run frees the lock at once, and no child of this script inherits the lock's descriptor. The holder
# writes the owner's pid into the lock file and answers "locked" or "held <pid>" on fd 9.
exec 9< <("$PY" -P -c 'import fcntl, os, sys, time
lock, owner = sys.argv[1], int(sys.argv[2])
f = open(lock, "a+")
try:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    f.seek(0); print("held", f.read().strip() or "?", flush=True); sys.exit(1)
f.seek(0); f.truncate(); f.write(f"{owner}\n"); f.flush()
print("locked", flush=True)
sys.stdout.close(); os.close(1)
try:   # macOS/BSD: wake the moment the owner exits
    import select
    kq = select.kqueue()
    kq.control([select.kevent(owner, select.KQ_FILTER_PROC, select.KQ_EV_ADD, select.KQ_NOTE_EXIT)], 0)
    while not kq.control(None, 1, 60):
        pass
except (AttributeError, OSError):   # no kqueue, or the owner is already gone: poll
    while True:
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        time.sleep(1)' "$LOCK" "$$" 2>/dev/null)
lock_state="" _lock_owner=""; read -r -u 9 lock_state _lock_owner || true
exec 9<&-
case "$lock_state" in
  locked) ;;
  held) echo "$(date -u +%FT%TZ) remediate.flock is held: another remediation run (pid ${_lock_owner:-?}) or a deploy"; exit 0 ;;
  *) echo "$(date -u +%FT%TZ) could not take $LOCK (the pipx python $PY did not answer)"; exit 1 ;;
esac

latest="$(latest_run)"
today="$(date +%F)"
if [[ "$(basename "${latest:-none}")" != "$today" ]]; then
  echo "$(date -u +%FT%TZ) today's scan has not completed yet (latest: $(basename "${latest:-none}"))"; exit 0
fi
[[ -f "$latest/remediation.done" ]] && exit 0
# summary.md exists before the daily run's agent phase starts, so a tick between the two would mark the day done
# before the agent ran. Wait while the daily run holds its lock, and (agent on) until agent/status.json records
# "finished"; a crashed agent that never records it stops being waited for at GIVE_UP_HOUR.
if "$PY" -P -c 'import sys; from agentteams.vulnscan import daily; sys.exit(0 if daily.run_in_progress() else 1)' 2>/dev/null; then
  echo "$(date -u +%FT%TZ) today's daily run (scan or agent phase) is still in progress"; exit 0
fi
if ! "$PY" -P - "$latest/agent/status.json" <<'EOF' 2>/dev/null; then
import json, sys
from agentteams.vulnscan import daily
if not (daily.load_config().get("agent") or {}).get("enabled"):
    sys.exit(0)
try:
    sys.exit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("finished") else 1)
except (OSError, ValueError):
    sys.exit(1)
EOF
  if (( 10#$(date +%H) < GIVE_UP_HOUR )); then
    echo "$(date -u +%FT%TZ) today's agent run has not finished (no \"finished\" in agent/status.json)"; exit 0
  fi
  echo "$(date -u +%FT%TZ) today's agent run never recorded \"finished\"; remediating anyway (past ${GIVE_UP_HOUR}:00)"
fi
prev="$(sed -n 's/^attempt=//p' "$latest/remediation.blocked" 2>/dev/null || true)"; [[ "$prev" =~ ^[0-9]+$ ]] || prev=0
ATTEMPT=$(( prev + 1 ))
ATTEMPT_START="$(date -u +%FT%TZ)"   # ledger rows at or after this are this attempt's

RLOG="$latest/remediation.log"
# remediation.log keeps every run of the day; the report and the notification read only this run's part of it
RLOG_FROM=$(( $(stat -f%z "$RLOG" 2>/dev/null || echo 0) + 1 ))
exec > >(tee -a "$RLOG") 2>&1
say "vulnscan remediation $(date '+%F %T') — run $(basename "$latest")"
problems=()

say "Preflight"
[[ -n "$REPO_ORG" ]] || problems+=("VULNSCAN_GITHUB_ORG not set in $REMEDIATION_CONF: no repo can be handed off")
command -v goose >/dev/null && ok "goose $(goose --version 2>/dev/null | head -1)" || problems+=("goose not on PATH")
host="$(awk '/^OPENROUTER_HOST:/{print $2; exit}' "$HOME/.config/goose/config.yaml" 2>/dev/null || true)"
if [[ -n "$host" ]] && curl -s -o /dev/null -m 5 "$host"; then ok "route proxy answering at $host"
else problems+=("goose route proxy not answering at ${host:-?}"); fi
grants="$("${VS[@]}" grant list 2>&1 || true)"
"$PY" -P - "$grants" <<'EOF' || problems+=("grant check: see log")
import datetime as dt, json, sys
now, bad, repo, host = dt.datetime.now(dt.timezone.utc), 0, False, False
for line in sys.argv[1].splitlines():
    if line.startswith("INVALID") and "exceed the aggregate cap" in line:
        print(f"PROBLEM {line}"); bad = 1
    if not line.startswith("{"):
        continue
    g = json.loads(line)
    days = (dt.datetime.fromisoformat(g["expires"]) - now).days
    repo |= "stage-fix" in g["ops"]; host |= "server-dryrun" in g["ops"]
    print(f"grant {g['grant_id']} ({', '.join(g['ops'])}) expires in {days} day(s)")
    if days < 7:
        print(f"PROBLEM grant {g['grant_id']} expires in {days} day(s): renew it with your key (operator script steps 2/3)"); bad = 1
if not repo: print("PROBLEM no valid repo grant (stage-fix/open-pr)"); bad = 1
if not host: print("PROBLEM no valid server grant (server-dryrun)")
sys.exit(bad)
EOF

if halts="$("${VS[@]}" halts 2>&1)"; then ok "no escalation or HALT blocks remediation"
else
  echo "$halts" | sed 's/^/  /'
  warn "an escalation or HALT blocks remediation: resolve it (vulnscan halts; vulnscan resolve <id>); executor ops refuse until then"
  problems+=("open escalation or HALT (vulnscan halts)")
fi

# Docker: start it when needed. When Docker, goose or the route proxy is not usable, the remediation steps
# are skipped for this attempt and the attempt counts as blocked (retried on the next tick, bounded below).
exec_ok=1
if docker_line="$(bash "$SRC/vulnscan-ensure-docker.sh")"; then ok "$docker_line"
else exec_ok=0; echo "PROBLEM docker not ready (attempt $ATTEMPT/$MAX_ATTEMPTS): $docker_line"; problems+=("docker: $docker_line"); fi
if [[ " ${problems[*]-} " == *"goose"* || " ${problems[*]-} " == *"route proxy"* ]]; then
  exec_ok=0; echo "PROBLEM cannot run orchestrators (attempt $ATTEMPT/$MAX_ATTEMPTS): ${problems[*]}"
fi

if [[ "$exec_ok" == 1 ]]; then
  say "Target repo setup (goose teams, manager roster)"
  bash "$SRC/vulnscan-target-agents.sh" --yes || problems+=("target setup exited $?")
  say "Runtime standard, pending + new handoffs, server dry runs"
  if [[ -f "$OPERATOR_ACTIONS" ]]; then
    bash "$OPERATOR_ACTIONS" --yes 12 13 9 16 15 || problems+=("remediation steps exited $?")
  else
    echo "PROBLEM no site-local $OPERATOR_ACTIONS: handoff steps not run"; problems+=("no vulnscan-operator-actions.sh")
  fi
else
  warn "remediation steps skipped this attempt: Docker, goose or the route proxy is not usable (the next 30-minute tick retries)"
fi
# ------------------------------------------------------------------------------------------- report
THIS_RUN="$(mktemp)"; tail -c "+$RLOG_FROM" "$RLOG" > "$THIS_RUN"
# The outcome comes from this attempt's ledger rows (agentteams.vulnscan.remediation_report), which also writes
# remediation.md. If the report cannot run at all, it fails closed: counted as infra, so the attempt is retried.
# The agent's own executor requests ran before this attempt: report them too (never counted toward a retry)
# (--agent-since=VALUE form: a value from the agent's file can never be read as an option)
AGENT_ARGS=()
while IFS= read -r a; do [[ -n "$a" ]] && AGENT_ARGS+=("$a"); done < <("$PY" -P -c 'import json, sys
try: s = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError): s = {}
for k in ("started", "finished"):
    v = s.get(k) if isinstance(s, dict) else None
    if isinstance(v, str) and v and "\n" not in v:
        print(f"--agent-{'since' if k == 'started' else 'until'}={v}")' "$latest/agent/status.json" 2>/dev/null || true)
if ! OUTCOME="$("${VS[@]}" remediation-report "$STATE_DIR/ledger.jsonl" "$ATTEMPT_START" --log "$THIS_RUN" \
                --run "$(basename "$latest")" --attempt "$ATTEMPT" --out "$latest/remediation.md" \
                ${AGENT_ARGS[@]+"${AGENT_ARGS[@]}"})"; then
  OUTCOME='{"infra_count": 1, "error": "remediation-report did not run"}'
  printf '# Vulnscan remediation — %s (attempt %s)\n\n## Needs you\n\n- remediation outcome unknown: %s\n' \
    "$(basename "$latest")" "$ATTEMPT" "remediation-report did not run (is the installed package current?)" \
    > "$latest/remediation.md"
fi
cat "$latest/remediation.md"
infra_n="$("$PY" -P -c 'import json, sys
o = json.loads(sys.argv[1].strip().splitlines()[-1])
print(int(o["infra_count"]))' "$OUTCOME" 2>/dev/null || echo 1)"
# infra = an executor refusal for want of Docker, or a preflight that skipped the steps; both are retryable
if [[ "$exec_ok" != 1 ]]; then infra_n=$((infra_n + 1)); fi

prs="$({ grep -oE 'https://github\.com/\S+/pull/[0-9]+' "$THIS_RUN" || true; } | sort -u | wc -l | tr -d ' ')"
rm -f "$THIS_RUN"
if (( infra_n == 0 )); then
  rm -f "$latest/remediation.blocked"; touch "$latest/remediation.done"
  notify_user "Run $(basename "$latest"): $prs PR(s) opened or claimed${problems:+; ${#problems[@]} problem(s)}. Report: remediation.md"
elif (( ATTEMPT >= MAX_ATTEMPTS )) || (( 10#$(date +%H) >= GIVE_UP_HOUR )); then
  rm -f "$latest/remediation.blocked"; touch "$latest/remediation.done"
  warn "giving up for today after attempt $ATTEMPT: $infra_n infrastructure refusal(s) — see Needs you"
  notify_user "Remediation GAVE UP for $(basename "$latest") after $ATTEMPT attempt(s): Docker, goose or the tools container not usable. Report: remediation.md"
else
  printf 'attempt=%s\nat=%s\ninfra=%s\n' "$ATTEMPT" "$ATTEMPT_START" "$infra_n" > "$latest/remediation.blocked"
  warn "blocked (attempt $ATTEMPT/$MAX_ATTEMPTS): $infra_n infrastructure refusal(s); the next 30-minute tick retries"
  notify_user "Remediation blocked (attempt $ATTEMPT/$MAX_ATTEMPTS): Docker, goose or the tools container not usable; retrying in 30 min. Report: remediation.md"
fi
say "Done. Report: $latest/remediation.md"
