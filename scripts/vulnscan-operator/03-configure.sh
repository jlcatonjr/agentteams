#!/usr/bin/env bash
# 03 — configure vulnscan, the ops team, your operator key and agent mode (walkthrough Steps 1, 4, 5, 6).
# Run in your own interactive terminal (never in an agent session).
#
#   Step 1  `init` with the sandbox repo root (only if no vulnscan.json exists yet)
#   Step 4  render a goose team with the vulnerability_remediation capability in ~/ops-team
#   Step 5  create your Ed25519 operator key (KEY_ID, default op1) and install the public key for vulnscan
#   Step 6  write the `agent` block: OpenRouter, GLM 5.3, $2 budget, enabled: false
#           and create the OpenRouter key file from the agentteams checkout's .env (its OPENROUTER_API_KEY line;
#           else goose-backend.sh, goose's keychain entry, or a hidden-input paste); never echoed or logged
#
# Every step is skipped when its result already exists. Nothing here signs anything or starts an agent.
# Usage:   KEY_ID=op1 bash scripts/vulnscan-operator/03-configure.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
KEY_ID="${KEY_ID:-op1}"
TEAM="$HOME/ops-team"
ROOT="$HOME/vulnscan-sandbox-root"
CFG="$HOME/.config/agentteams"
SECRETS="$HOME/.config/agentteams-secrets"          # outside ~/.config/agentteams and outside the team dir
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
[ -t 0 ] || { echo "run this in an interactive terminal" >&2; exit 2; }

echo "== Step 1: config"
if [ -f "$CFG/vulnscan.json" ]; then echo "exists: $CFG/vulnscan.json (left unchanged)"
else "${VS[@]}" init --repo-root "$ROOT"; fi

echo "== Step 4: ops team in $TEAM"
if [ -f "$TEAM/.goose/recipes/vuln-remediation.yaml" ]; then echo "exists (left unchanged)"
else
  mkdir -p "$TEAM"; chmod 755 "$TEAM"
  [ -f "$TEAM/brief.json" ] || printf '%s\n' '{' \
    '  "project_goal": "Remediate known vulnerabilities in my repositories and servers",' \
    '  "capabilities": ["vulnerability_remediation"]' '}' > "$TEAM/brief.json"
  (cd "$TEAM" && "$(dirname "$PY")/agentteams" --description brief.json --framework goose --project .)
fi

echo "== Step 5: operator key $KEY_ID"
PRIV="$CFG/keys/decision-signing-$KEY_ID.pem"
if [ -f "$PRIV" ]; then echo "exists: $PRIV (left unchanged)"
else bash "$REPO/references/authorized-verify-keys/provision-operator-signing-key.sh" --team-dir "$TEAM/.goose/recipes" "$KEY_ID"; fi
mkdir -p "$CFG/verify-keys"; chmod 700 "$CFG/verify-keys"
if [ ! -f "$CFG/verify-keys/$KEY_ID.pub.pem" ]; then
  openssl pkey -in "$PRIV" -pubout -out "$CFG/verify-keys/$KEY_ID.pub.pem"
  chmod 644 "$CFG/verify-keys/$KEY_ID.pub.pem"
fi
echo "verify key: $CFG/verify-keys/$KEY_ID.pub.pem"

echo "== Step 6: OpenRouter key file and the agent block"
mkdir -p "$SECRETS"; chmod 700 "$SECRETS"
KEYFILE="$SECRETS/openrouter.env"
if [ -f "$KEYFILE" ]; then echo "exists: $KEYFILE (left unchanged)"
else
  # Sources, first hit wins: 1) the agentteams checkout's .env, 2) ~/.config/goose/goose-backend.sh,
  # 3) goose's keychain entry, 4) a hidden-input paste. The key is never printed, passed as a command
  # argument, or written anywhere but the mode-600 key file.
  ORKEY=""
  TRIED_KEYCHAIN=0
  # 1) the agentteams checkout's own .env (gitignored): only its OPENROUTER_API_KEY line is read; the
  #    file is never sourced, so nothing else in it reaches this script. From a worktree, the main
  #    checkout's .env is used. Override with AGENTTEAMS_ENV_FILE.
  MAIN="$(cd "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
  ENVF="${AGENTTEAMS_ENV_FILE:-$MAIN/.env}"
  if [ -f "$ENVF" ]; then
    echo "reading OPENROUTER_API_KEY from $ENVF (that line only)"
    ORKEY="$(grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=' "$ENVF" \
      | sed -E "s/^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=//; s/\r$//; s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true)"
  fi
  # 2) the key your goose OpenRouter backend uses: ~/.config/goose/goose-backend.sh's own resolver
  #    (GOOSE_OPENROUTER_ENV_FILE, then an inherited OPENROUTER_API_KEY), run in a subshell so nothing
  #    it defines or exports leaks into this script.
  GB="${GOOSE_BACKEND_SH:-$HOME/.config/goose/goose-backend.sh}"
  if [ -z "$ORKEY" ] && [ -f "$GB" ]; then
    echo "reading OPENROUTER_API_KEY the way $GB resolves it"
    ORKEY="$(bash -c 'source "$1" >/dev/null 2>&1; declare -F _goose_resolve_key >/dev/null && _goose_resolve_key' _ "$GB" 2>/dev/null || true)"
  fi
  # 3) goose's keychain entry: `goose configure` keeps provider secrets as JSON in the login keychain
  #    (service "goose", account "secrets"); macOS may ask you to allow access. GOOSE_KEY=0 skips it.
  if [ -z "$ORKEY" ] && [ "${GOOSE_KEY:-1}" = 1 ] && command -v security >/dev/null; then
    TRIED_KEYCHAIN=1
    echo "reading OPENROUTER_API_KEY from goose's keychain entry (allow access if macOS asks)"
    ORKEY="$(security find-generic-password -s goose -a secrets -w 2>/dev/null | "$PY" -P -c '
import json, sys
raw = sys.stdin.read().strip()
try:
    data = json.loads(raw)
except ValueError:
    try:
        data = json.loads(bytes.fromhex(raw).decode())
    except ValueError:
        data = {}
value = data.get("OPENROUTER_API_KEY", "") if isinstance(data, dict) else ""
print(value if isinstance(value, str) else "")' || true)"
  fi
  # 4) a hidden-input paste
  if [ -z "$ORKEY" ]; then
    if [ "$TRIED_KEYCHAIN" = 1 ]; then
      echo "no OPENROUTER_API_KEY in the .env, goose-backend.sh or goose's keychain entry (or access was denied)"
    else
      echo "no OPENROUTER_API_KEY in the .env or goose-backend.sh (goose's keychain entry was not read)"
    fi
    printf 'Paste your OpenRouter API key instead (input hidden), then Enter: '
    read -rs ORKEY; echo
  fi
  case "$ORKEY" in sk-or-*) ;; *) echo "that does not look like an OpenRouter key (sk-or-...)" >&2; exit 2;; esac
  (umask 077; printf 'OPENROUTER_API_KEY=%s\n' "$ORKEY" > "$KEYFILE")
  unset ORKEY
  echo "wrote $KEYFILE (mode 600)"
fi
"$PY" -P - "$CFG/vulnscan.json" "$TEAM" "$KEYFILE" <<'PY'
import json, sys
from pathlib import Path
cfg_path, team, keyfile = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
cfg = json.loads(cfg_path.read_text())
agent = cfg.get("agent") or {}
agent.update({"enabled": False, "team_dir": team, "key_file": keyfile, "upstream": "https://openrouter.ai",
              "model": "z-ai/glm-5.3", "budget_usd": 2.0, "max_minutes": 45})
cfg["agent"] = agent
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
from agentteams.vulnscan import daily, agent_run
daily.load_config()
print("agent block written and valid; team_dir check:", agent_run.team_dir_problem(team, (keyfile,)) or "ok")
PY
echo
echo "Next: start Docker Desktop, then  bash $REPO/scripts/vulnscan-agent-image.sh"
