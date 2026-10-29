# Shared helpers for the unattended remediation scripts in scripts/vulnscan-remediation/ (sourced, not run).
# Handoff model: docs_src/vulnscan-automation-walkthrough.md ("Unattended remediation runner").
#
# Site settings come from $CFG_DIR/vulnscan-remediation.conf (KEY=VALUE lines, read, never sourced), or from the
# environment variable of the same name, which wins:
#   VULNSCAN_REPOS_ROOT   folder holding the checkouts remediation may touch (default: $HOME/githubrepositories)
#   VULNSCAN_GITHUB_ORG   GitHub owner whose repos get handoffs; unset = no repo is handed off (fails closed)
#   MANAGER_TEAM          the vulnscan team that issues directives (default: ops-team)
#   MANAGER_TEAM_DIR      that team's directory (default: $HOME/$MANAGER_TEAM); its sandbox/confine-run.sh is the
#                         fallback confinement launcher

PY="$HOME/.local/pipx/venvs/agentteams/bin/python"      # the interpreter the launchd job runs
AT="$HOME/.local/pipx/venvs/agentteams/bin/agentteams"   # same install's agentteams CLI
VS=("$PY" -P -m agentteams.vulnscan)
CFG_DIR="${AGENTTEAMS_VULNSCAN_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/agentteams}"
STATE_DIR="${AGENTTEAMS_VULNSCAN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/vulnscan}"
KIT="$CFG_DIR/server-kit"
REMEDIATION_CONF="$CFG_DIR/vulnscan-remediation.conf"
conf_get() {   # conf_get <KEY> <default>  → the environment's value, else the conf file's, else the default
  local v="${!1:-}"
  if [[ -z "$v" && -f "$REMEDIATION_CONF" ]]; then
    v="$(awk -F= -v k="$1" '$0 !~ /^[[:space:]]*#/ && $1 == k { sub(/^[^=]*=/, ""); print; exit }' "$REMEDIATION_CONF")"
    v="${v%\"}"; v="${v#\"}"
  fi
  [[ "$v" == "~/"* ]] && v="$HOME/${v#\~/}"
  echo "${v:-$2}"
}
REPOS_ROOT="$(conf_get VULNSCAN_REPOS_ROOT "$HOME/githubrepositories")"
REPO_ORG="$(conf_get VULNSCAN_GITHUB_ORG "")"
MANAGER_TEAM="$(conf_get MANAGER_TEAM ops-team)"          # the vulnscan team (agent.team_dir) as directive issuer
MANAGER_TEAM_DIR="$(conf_get MANAGER_TEAM_DIR "$HOME/$MANAGER_TEAM")"
if [[ -n "$REPO_ORG" && ! "$REPO_ORG" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "VULNSCAN_GITHUB_ORG is not a GitHub owner name: $REPO_ORG (ignored)" >&2; REPO_ORG=""
fi
VK="$REPOS_ROOT" VK_ORG="$REPO_ORG"   # the names a site-local vulnscan-operator-actions.sh may still use
MGMT_KEYFILE="${MGMT_KEYFILE:-$CFG_DIR/keys/management-signing.key}"   # AGENTTEAMS_MANAGEMENT_SIGNING_KEY value
APPROVER="${APPROVER:-operator:$(git config --global user.name 2>/dev/null || whoami)}"
HANDOFF_DIR=".vulnscan-handoff"                          # per target repo; listed in .git/info/exclude, never tracked
# Standard for every remediation agent (the daily agent and every handoff session): goose + OpenRouter + GLM 5.3,
# through the local route proxy your goose config pins (OPENROUTER_HOST). Never Anthropic or Copilot models.
GOOSE_PROVIDER_STD="${GOOSE_PROVIDER_STD:-openrouter}"
GOOSE_MODEL_STD="${GOOSE_MODEL_STD:-$("$PY" -P -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("agent",{}).get("model") or "z-ai/glm-5.3")
except Exception: print("z-ai/glm-5.3")' "$CFG_DIR/vulnscan.json" 2>/dev/null || echo z-ai/glm-5.3)}"
TEAM_BRANCH_PREFIX="chore/goose-agent-team"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*"; }
bad()  { printf '\033[31mACTION\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK\033[0m %s\n' "$*"; }
# Auto mode (VULNSCAN_AUTO=1, or --yes on a script): routine steps are approved without asking and each approval
# is appended to the clearance log; operator-only gates (grant signing, destructive deletes, server --apply) are
# skipped and reported, never approved; free-text prompts take their defaults. The operator authorizes auto mode
# by passing --yes or by installing the scheduled job (vulnscan-remediate-auto.sh --install).
AUTO="${VULNSCAN_AUTO:-0}"
CLEARANCE_LOG="$STATE_DIR/operator-clearances.log"
clearance() { mkdir -p "$STATE_DIR"; printf '%s\t%s\tauto-approved\t%s\n' "$(date -u +%FT%TZ)" "$APPROVER" "$1" >> "$CLEARANCE_LOG"; }
ask()  {
  if [[ "$AUTO" == 1 ]]; then echo "  auto: yes — $1"; clearance "$1"; return 0; fi
  local r; read -r -p "$1 [y/N] " r </dev/tty; [[ "$r" =~ ^[Yy]$ ]]
}
gate() {   # operator-only decisions: asked interactively, skipped (never approved) in auto mode
  if [[ "$AUTO" == 1 ]]; then warn "operator-only, skipped in auto mode: $1"; return 1; fi
  ask "$1"
}
prompt_or_default() {   # prompt_or_default <question> <default>  → answer on stdout
  local r=""
  if [[ "$AUTO" != 1 ]]; then read -r -p "$1 [$2]: " r </dev/tty; fi
  echo "${r:-$2}"
}
notify_user() {   # best-effort desktop notification (works from a launchd user agent)
  osascript -e "display notification \"${1//\"/\'}\" with title \"vulnscan remediation\"" >/dev/null 2>&1 || true
}

# Unattended goose session: confined with the emitted sandbox/confine-run.sh, started IN the repo (launchd's cwd
# is /). Writes only to the repo's handoff folder (packet, verdict, review worktree, its own branch's files), the
# repo's git dir minus hooks/ and config (a planted hook would later run unconfined) and goose's own dirs; egress
# only to the loopback route proxy that forwards to OpenRouter; killed after GOOSE_TIMEOUT_SEC. Fails closed: no
# confinement, no run.
GOOSE_TIMEOUT_SEC="${GOOSE_TIMEOUT_SEC:-2700}"
GOOSE_MAX_TURNS="${GOOSE_MAX_TURNS:-150}"
goose_headless() {   # goose_headless <home> <recipe> <log>  → exit status (124 timeout, 3 refused to run)
  local home="$1" recipe="$2" log="$3" confine proxy common scratch
  confine="$home/sandbox/confine-run.sh"; [[ -f "$confine" ]] || confine="$MANAGER_TEAM_DIR/sandbox/confine-run.sh"
  [[ -f "$confine" ]] || { warn "no sandbox/confine-run.sh: refusing to run goose unconfined"; return 3; }
  proxy="$(awk '/^OPENROUTER_HOST:/{print $2; exit}' "$HOME/.config/goose/config.yaml" 2>/dev/null | sed -E 's#^https?://##; s#/.*##')"
  proxy="${proxy/localhost/127.0.0.1}"
  [[ "$proxy" =~ ^127\.0\.0\.1:[0-9]+$ ]] || { warn "goose OPENROUTER_HOST is not a loopback route proxy ($proxy): refusing unconfined egress"; return 3; }
  common="$(git -C "$home" rev-parse --git-common-dir)"; [[ "$common" = /* ]] || common="$home/$common"
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-goose.XXXXXX")"
  mkdir -p "$HOME/.local/share/goose" "$HOME/.local/state/goose" "$HOME/.config/goose" "$home/$HANDOFF_DIR"
  local protect=() pth
  for pth in "$common/hooks" "$common/config"; do [[ -e "$pth" ]] && protect+=(--protect "$pth"); done
  cd "$home" || return 3
  "$PY" -P -c 'import os, signal, subprocess, sys
p = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    sys.exit(p.wait(timeout=int(sys.argv[1])))
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL); sys.exit(124)' "$GOOSE_TIMEOUT_SEC" \
    bash "$confine" --scratch "$scratch" --egress proxy --proxy "$proxy" \
      --writable "$home/$HANDOFF_DIR" --writable "$common" "${protect[@]}" --writable "$HOME/.local/share/goose" \
      --writable "$HOME/.local/state/goose" --writable "$HOME/.config/goose" --cpu-max "$GOOSE_TIMEOUT_SEC" \
      -- goose run --provider "$GOOSE_PROVIDER_STD" --model "$GOOSE_MODEL_STD" --recipe "$recipe" \
         --max-turns "$GOOSE_MAX_TURNS" > "$log" 2>&1
}

latest_run() { ls -1d "$STATE_DIR"/20??-??-?? 2>/dev/null | tail -1; }

# Git toplevels of the run's fixable package findings under $REPOS_ROOT whose origin is github.com/$REPO_ORG/*
# (one per line). No VULNSCAN_GITHUB_ORG configured: none (fails closed; a handoff never reaches a repo by default).
fix_repos() {
  local t top url
  if [[ -z "$REPO_ORG" ]]; then
    echo "  no VULNSCAN_GITHUB_ORG in $REMEDIATION_CONF: no repository is handed off" >&2; return 0
  fi
  "$PY" -P - "$1/summary.json" <<'EOF' | sort -u | while IFS= read -r t; do
import json, sys
for f in json.load(open(sys.argv[1]))["findings"]:
    if f.get("kind") == "package" and f.get("fix") and not f.get("malicious") and f["target"].startswith("/"):
        print(f["target"])
EOF
    [[ "$t" == "$REPOS_ROOT"/* ]] || continue
    top="$(git -C "$t" rev-parse --show-toplevel 2>/dev/null)" || continue
    url="$(git -C "$top" remote get-url origin 2>/dev/null || true)"
    if [[ "$url" =~ github\.com[:/]$REPO_ORG/ ]]; then echo "$top"; else echo "  skipped (not $REPO_ORG: ${url:-no origin}): $top" >&2; fi
  done | sort -u
}

# Where a repo's goose team lives: the checkout itself, else the team worktree this workflow creates
# (<repo>.worktrees/goose-agent-team on branch chore/goose-agent-team-<date>). Empty if neither has one.
team_worktree() { echo "$1.worktrees/goose-agent-team"; }
goose_home() {
  local repo="$1" wt; wt="$(team_worktree "$repo")"
  if [[ -f "$repo/.goose/recipes/orchestrator.yaml" ]]; then echo "$repo"
  elif [[ -f "$wt/.goose/recipes/orchestrator.yaml" ]]; then echo "$wt"; fi
  return 0
}

# "goose|<home>|<recipes dir>" for a repo's goose orchestrator; empty if it has none yet.
orchestrator_of() {
  local home; home="$(goose_home "$1")"
  if [[ -n "$home" ]]; then echo "goose|$home|$home/.goose/recipes"; fi
  return 0
}

# The repo's brief: the agentteams build description, else brief.json at the root.
brief_of() {
  local repo="$1" f
  for f in "$repo/.github/agents/_build-description.json" "$repo/brief.json" "$repo/.claude/agents/_build-description.json"; do
    [[ -f "$f" ]] && { echo "$f"; return; }
  done
  return 0
}

manager_rostered() {   # $1 = team dir
  [[ -f "$1/references/authorized-managers.txt" ]] &&
    grep -vE '^\s*(#|$)' "$1/references/authorized-managers.txt" | sed 's/^@//' | tr 'A-Z' 'a-z' | grep -qx "$(tr 'A-Z' 'a-z' <<<"$MANAGER_TEAM")"
}

# Run a command with the management signing key in ITS environment only (never exported).
with_mgmt_key() {
  [[ -f "$MGMT_KEYFILE" ]] || { warn "no management signing key at $MGMT_KEYFILE (run vulnscan-target-agents.sh first)"; return 1; }
  AGENTTEAMS_MANAGEMENT_SIGNING_KEY="$(cat "$MGMT_KEYFILE")" "$@"
}

repo_state() {   # one-line branch / dirty summary
  printf 'branch=%s dirty=%s' "$(git -C "$1" branch --show-current)" "$(git -C "$1" status --porcelain | wc -l | tr -d ' ')"
}
