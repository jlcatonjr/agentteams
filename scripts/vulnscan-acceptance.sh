#!/usr/bin/env bash
# vulnscan acceptance checks (walkthrough Part D), READ-ONLY. Run on the operator's machine.
#
# It changes nothing: no install, no signing, no container start, no push. It reports, per check,
# PASS / FAIL / SKIP with the reason, and writes the report to a file OUTSIDE any repository
# (default: ~/vulnscan-acceptance-<date>.txt) so it is never committed by accident. The report holds
# host-specific facts (tool versions, token scope names, host names); do not paste it into a public place.
#
# Usage:   bash scripts/vulnscan-acceptance.sh [--ops-repo OWNER/REPO] [--host NAME] [--out FILE]
#          VULNSCAN_PY=/path/to/python bash scripts/vulnscan-acceptance.sh
#
# VULNSCAN_PY: the interpreter of your agentteams install (default: the pipx venv's python if agentteams
# is installed with pipx, else python3).
set -uo pipefail

OPS_REPO=""; HOST=""; OUT="$HOME/vulnscan-acceptance-$(date +%Y-%m-%d).txt"
while [ $# -gt 0 ]; do case "$1" in
  --ops-repo) OPS_REPO="${2-}"; shift;; --host) HOST="${2-}"; shift;; --out) OUT="${2-}"; shift;;
  -h|--help) sed -n '2,16p' "$0"; exit 0;;
  *) echo "unknown option: $1" >&2; exit 2;; esac; shift; done

PY="${VULNSCAN_PY:-}"
if [ -z "$PY" ] && command -v pipx >/dev/null; then
  venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)"
  [ -n "$venvs" ] && [ -x "$venvs/agentteams/bin/python" ] && PY="$venvs/agentteams/bin/python"
fi
PY="${PY:-python3}"
VS=("$PY" -P -m agentteams.vulnscan)

exec > >(tee "$OUT") 2>&1
pass=0; fail=0; skip=0
res(){ printf '%-5s %-34s %s\n' "$1" "$2" "$3"; case "$1" in PASS) pass=$((pass+1));; FAIL) fail=$((fail+1));; *) skip=$((skip+1));; esac; }

echo "vulnscan acceptance — $(date '+%Y-%m-%d %H:%M %Z') — report: $OUT"
echo "interpreter: $PY"
echo

# 0. the install
if "$PY" -c 'import agentteams.vulnscan' 2>/dev/null; then
  where="$("$PY" -c 'import agentteams,sys; print(agentteams.__file__)')"
  res PASS "agentteams importable" "$where"
  if "$PY" -c 'import cryptography' 2>/dev/null; then res PASS "signing extra" "cryptography present"
  else res FAIL "signing extra" "cryptography missing: install agentteams[signing]"; fi
else
  res FAIL "agentteams importable" "not importable with $PY (set VULNSCAN_PY or pipx install it)"
fi

# 1. feeds
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://api.osv.dev/v1/vulns/GHSA-ffqj-6fqr-9h24 || true)"
[ "$code" = 200 ] && res PASS "OSV.dev reachable" "HTTP 200" || res FAIL "OSV.dev reachable" "HTTP ${code:-none}"
"${VS[@]}" runtimes >/dev/null 2>&1; rc=$?
case "$rc" in 0|1) res PASS "release feeds (runtimes)" "exit $rc";; 3) res FAIL "release feeds (runtimes)" "exit 3: a feed is unreachable (run: ${VS[*]} runtimes)";;
  *) res FAIL "release feeds (runtimes)" "exit $rc";; esac

# 2. config and schedule
cfgdir="${AGENTTEAMS_VULNSCAN_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/agentteams}"
if [ -f "$cfgdir/vulnscan.json" ]; then
  if "$PY" -c 'from agentteams.vulnscan import daily; daily.load_config()' 2>/tmp/vs-acc-cfg.$$; then
    res PASS "vulnscan.json loads" "$cfgdir/vulnscan.json"
  else res FAIL "vulnscan.json loads" "$(tail -1 /tmp/vs-acc-cfg.$$)"; fi
  rm -f /tmp/vs-acc-cfg.$$
  st="$("${VS[@]}" schedule status 2>&1)"
  case "$st" in *"installed: True"*) res PASS "daily job installed" "$(printf '%s' "$st" | grep -E '^(mechanism|last run)' | tr '\n' ' ')";;
    *) res SKIP "daily job installed" "not installed yet (Step 3: ${VS[*]} schedule install --apply)";; esac
else
  res SKIP "vulnscan.json loads" "no config yet (walkthrough Step 1: ${VS[*]} init --repo-root ~/code)"
fi

# 3. one server audit (dry, read-only L1)
if [ -n "$HOST" ]; then
  kit="$cfgdir/server-kit"
  if [ -x "$kit/remote.sh" ] || [ -f "$kit/remote.sh" ]; then
    bash "$kit/remote.sh" "$HOST" L1-audit.sh >/dev/null 2>&1; rc=$?
    case "$rc" in 0|1) res PASS "server L1 audit ($HOST)" "exit $rc";; 4) res FAIL "server L1 audit ($HOST)" "exit 4: COMPROMISE indicators — read the log";;
      *) res FAIL "server L1 audit ($HOST)" "exit $rc (255 unreachable, 2 config, 3 incomplete)";; esac
  else res SKIP "server L1 audit ($HOST)" "no server kit at $kit (Step 2)"; fi
else res SKIP "server L1 audit" "no --host given"; fi

# 4. docker
if command -v docker >/dev/null; then
  if docker info >/dev/null 2>&1; then res PASS "docker daemon" "$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  else res FAIL "docker daemon" "installed but not running (start Docker Desktop)"; fi
else res FAIL "docker daemon" "docker not installed"; fi
sf="${AGENTTEAMS_VULNSCAN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/vulnscan}/agent-selftest.json"
if [ -f "$sf" ]; then
  "$PY" - "$sf" <<'PY' && res PASS "agent self-test record" "passed" || res FAIL "agent self-test record" "last self-test did not pass (Step 7)"
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("passed") is True else 1)
PY
else res SKIP "agent self-test record" "none yet (Step 7)"; fi

# 5. GitHub token
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  scopes="$(gh auth status 2>&1 | sed -n "s/.*Token scopes: //p" | tr -d "'")"
  res PASS "gh signed in" "scopes: ${scopes:-fine-grained token (no classic scopes)}"
  broad=""
  for s in admin:org admin:enterprise delete_repo admin:public_key admin:repo_hook admin:org_hook; do
    case ", $scopes," in *", $s,"*) broad="$broad $s";; esac
  done
  [ -n "$broad" ] && res FAIL "gh token is narrow" "broader than vulnscan needs:$broad (open-pr and publish need repo contents + pull requests; publish preflight needs Administration: read)" \
                  || res PASS "gh token is narrow" "no admin/delete scopes"
  if [ -n "$OPS_REPO" ]; then
    who="$(gh api user --jq .login 2>/dev/null)"
    others="$(gh api "repos/$OPS_REPO/collaborators" --jq '.[].login' 2>/dev/null | grep -vx "$who" | tr '\n' ' ')"; rc1=$?
    keys="$(gh api "repos/$OPS_REPO/keys" --jq length 2>/dev/null)"
    priv="$(gh api "repos/$OPS_REPO" --jq .private 2>/dev/null)"
    [ "$priv" = true ] && res PASS "ops repo private" "$OPS_REPO" || res FAIL "ops repo private" "private=$priv"
    [ -z "$others" ] && [ "$keys" = 0 ] && res PASS "ops repo access" "only you; 0 deploy keys" \
      || res FAIL "ops repo access" "other collaborators: ${others:-none}; deploy keys: ${keys:-unreadable}"
  else res SKIP "ops repo checks" "no --ops-repo given"; fi
else res FAIL "gh signed in" "run: gh auth login"; fi

# 8. shellcheck (contributors)
if command -v shellcheck >/dev/null; then
  kitsrc="$("$PY" -c 'import agentteams,pathlib; print(pathlib.Path(agentteams.__file__).parent/"data"/"server_kit")' 2>/dev/null)"
  if shellcheck -S error "$kitsrc"/*.sh >/dev/null 2>&1; then res PASS "server kit shellcheck" "no errors"
  else res FAIL "server kit shellcheck" "errors (run: shellcheck -S error $kitsrc/*.sh)"; fi
else res SKIP "server kit shellcheck" "shellcheck not installed (contributors only: brew install shellcheck)"; fi

echo
echo "summary: $pass pass, $fail fail, $skip skipped. Not covered here (need a live run): model metering"
echo "(check 5: read <run>/gateway/spend.json after a supervised agent-run) and SIGTERM cleanup (check 9)."
[ "$fail" -eq 0 ]
