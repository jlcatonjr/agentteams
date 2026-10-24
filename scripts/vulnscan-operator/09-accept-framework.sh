#!/usr/bin/env bash
# 09 — live acceptance of one agent framework's runtime driver (plan phases 3-5). Run in your own
# interactive terminal, after 01-03 and 02 (the sandbox repo), with Docker running.
#
#   1. checks vulnscan.json selects FRAMEWORK (agent.framework) with a model that framework can run;
#      with your confirmation it switches agent.framework / agent.model (MODEL=) and keeps a backup
#   2. builds that framework's image (vulnscan-agent-image.sh: the CLI's pinned, checksum-verified release)
#      and runs agent-selftest; prints every check and the CLI's tool inventory from its headless probe
#   3. signs a NARROW grant: stage-fix only, only ~/vulnscan-sandbox-root, 1 day, 5 uses
#      (you review every field and confirm with y; your key is named on this one command only)
#   4. daily scan (agent mode stays off), then ONE supervised agent-run (Ctrl-C stops it: exit 143)
#   5. prints the outcome, the spend, the gateway's refusal reasons (a CLI request the gateway's
#      allowlists refused: report these so the allowlist can be extended) and the CLI's error lines
# It never prints a key: spend.json, status.json and the self-test record hold none, and transcript lines
# are filtered to error lines and capped.
#
# Usage:   FRAMEWORK=claude MODEL=anthropic/claude-sonnet-4.5 KEY_ID=op1 \
#            bash scripts/vulnscan-operator/09-accept-framework.sh
set -euo pipefail

FRAMEWORK="${FRAMEWORK:-claude}"
MODEL="${MODEL:-}"
KEY_ID="${KEY_ID:-op1}"
ROOT="$HOME/vulnscan-sandbox-root"
CFGFILE="$HOME/.config/agentteams/vulnscan.json"
PRIV="$HOME/.config/agentteams/keys/decision-signing-$KEY_ID.pem"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
[ -t 0 ] || { echo "run this in an interactive terminal (signing asks you to confirm)" >&2; exit 2; }
[ -f "$PRIV" ] || { echo "no operator key $PRIV (run 03-configure.sh)" >&2; exit 2; }
[ -f "$CFGFILE" ] || { echo "no $CFGFILE (run 03-configure.sh)" >&2; exit 2; }
[ -d "$ROOT" ] || { echo "no sandbox repo root $ROOT (run 02-sandbox-repo.sh)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 2; }
"$PY" -P -c 'import sys; from agentteams.vulnscan import agent_drivers; agent_drivers.get_driver(sys.argv[1])' \
  "$FRAMEWORK" 2>/dev/null || { echo "FRAMEWORK=$FRAMEWORK has no driver in this agentteams" >&2; exit 2; }

echo "== 1/5 vulnscan.json selects $FRAMEWORK"
read -r CUR_FW CUR_MODEL ENABLED < <("$PY" -P -c 'import sys; from agentteams.vulnscan import daily, agent_run
a = agent_run.agent_cfg(daily.load_config()); print(a["framework"], a["model"], str(bool(a["enabled"])).lower())')
if [ "$ENABLED" = true ]; then
  echo "agent.enabled is true: the daily job would run the agent too. Set it false for the acceptance run." >&2; exit 2
fi
if [ "$CUR_FW" != "$FRAMEWORK" ] || { [ -n "$MODEL" ] && [ "$MODEL" != "$CUR_MODEL" ]; }; then
  NEW_MODEL="${MODEL:-$CUR_MODEL}"
  echo "vulnscan.json has agent.framework=$CUR_FW, agent.model=$CUR_MODEL"
  read -r -p "Switch to agent.framework=$FRAMEWORK, agent.model=$NEW_MODEL (backup kept)? [y/N] " ANS
  case "$ANS" in y|Y) ;; *) echo "left unchanged; set them yourself, then re-run" >&2; exit 1;; esac
  cp -p "$CFGFILE" "$CFGFILE.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  "$PY" -P - "$CFGFILE" "$FRAMEWORK" "$NEW_MODEL" <<'PY'
import json, sys
from pathlib import Path
p, fw, model = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
cfg = json.loads(p.read_text())
cfg.setdefault("agent", {}).update(framework=fw, model=model)
p.write_text(json.dumps(cfg, indent=2) + "\n")
from agentteams.vulnscan import daily
daily.load_config()   # validate_agent: refuses a model the framework cannot run (claude: Anthropic models only)
print(f"agent.framework={fw} agent.model={model}; config valid")
PY
fi
TEAM="$("$PY" -P -c 'from agentteams.vulnscan import daily, agent_run; print(agent_run.agent_cfg(daily.load_config())["team_dir"])')"
GOV="$TEAM/$("$PY" -P -c 'import sys, posixpath; from agentteams.vulnscan import agent_drivers
print(posixpath.dirname(agent_drivers.get_driver(sys.argv[1]).default_recipe))' "$FRAMEWORK")"
[ -d "$GOV" ] || { echo "no $GOV: render a $FRAMEWORK team with the vulnerability_remediation capability first" >&2; exit 2; }

echo "== 2/5 image and self-test ($FRAMEWORK)"
FRAMEWORK="$FRAMEWORK" VULNSCAN_PY="$PY" bash "$HERE/../vulnscan-agent-image.sh" || true
"$PY" -P - "$FRAMEWORK" <<'PY'
import json, re, sys
from agentteams.vulnscan import paths
# check names and stderr come from inside the container: terminal escapes and control characters are dropped
_CTRL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.?|[\x00-\x1f\x7f-\x9f]")
def clean(text):
    return _CTRL.sub("", str(text))
f = paths.state_dir() / "agent-selftest.json"
rec = json.loads(f.read_text()) if f.is_file() else {}
if rec.get("framework") != sys.argv[1]:
    sys.exit(f"the self-test record is for {rec.get('framework')!r}, not {sys.argv[1]!r}: the build or self-test did not run")
print(f"self-test: {'PASSED' if rec.get('passed') else 'FAILED'} (framework {rec.get('framework')})")
for k, v in sorted((rec.get("checks") or {}).items()):
    print(f"  {'ok  ' if v else 'FAIL'} {clean(k)[:80]}")
detail = rec.get("detail") or {}
if detail:
    print("tool inventory (the CLI's own report, headless against a local stub):")
    for k in ("init_tools", "request_tools", "unexpected_tools", "mcp_servers", "markers_fired",
              "canary_token_seen", "rc", "result_subtype"):
        print(f"  {k}: {json.dumps(detail.get(k))}")
    if detail.get("stderr_tail"):
        print("  stderr (tail): " + clean(str(detail["stderr_tail"]).replace("\n", " | "))[:300])
sys.exit(0 if rec.get("passed") else 1)
PY
"$PY" -P -c 'import sys; from agentteams.vulnscan import daily, agent_run
ok, why = agent_run.selftest_ok(daily.load_config()); print("self-test binding:", why); sys.exit(0 if ok else 1)' \
  || { echo "no passing self-test for this framework and image: stop here and report the checks above" >&2; exit 1; }

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-accept.XXXXXX")"
trap 'rmdir "$WORKDIR" 2>/dev/null || true' EXIT
cd "$WORKDIR"   # sign from an empty directory (never a repository, the team dir or $HOME)

echo "== 3/5 sign the narrow grant: stage-fix on $ROOT, 1 day, 5 uses (review every field, then answer y)"
if "${VS[@]}" grant list 2>/dev/null | grep -q '"stage-fix"'; then
  echo "an active grant already exists:"; "${VS[@]}" grant list
else
  AGENTTEAMS_DECISION_ED25519_KEYFILE="$PRIV" "${VS[@]}" grant sign --key-id "$KEY_ID" --days 1 --max-uses 5 \
    --op stage-fix --repo-root "$ROOT" --governance-dir "$GOV"
fi

echo "== 4/5 daily scan, then one supervised agent-run (Ctrl-C to stop it)"
set +e
"${VS[@]}" daily; rc=$?
echo "daily exit $rc (0 clean, 1 findings, 3 partially unscanned, 4 compromise)"
"${VS[@]}" agent-run; rc=$?
set -e
echo "agent-run exit $rc (0 completed, 3 not run or agent error, 143 stopped)"

echo "== 5/5 outcome, spend, refusals"
RUN="$(ls -1d "$HOME/.local/state/agentteams/vulnscan"/20*-*-* 2>/dev/null | tail -1)"
[ -n "$RUN" ] || { echo "no run directory found" >&2; exit 1; }
"$PY" -P - "$RUN" <<'PY'
import json, re, sys
from pathlib import Path
run = Path(sys.argv[1])
# transcript lines and refusal reasons hold model and client text: terminal escapes and control characters are dropped
_CTRL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.?|[\x00-\x1f\x7f-\x9f]")
def clean(text):
    return _CTRL.sub("", str(text))
def load(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None
status = load(run / "agent" / "status.json") or {}
print(clean(f"run: {run.name}  state: {status.get('run_state')}  exit: {status.get('exit')}  "
            f"report: {'yes' if status.get('report') else 'no'}"))
if status.get("reason"):
    print(f"reason: {clean(status['reason'])[:300]}")
spend = load(run / "gateway" / "spend.json") or {}
keys = ("calls", "refused", "unmetered", "upstream_errors", "spent_usd", "budget_usd", "prompt_tokens",
        "completion_tokens", "drained", "drain_cut")
print("spend: " + clean(", ".join(f"{k}={spend.get(k)}" for k in keys)))
reasons = spend.get("refusal_reasons") or []
print(f"gateway refusals ({len(reasons)} distinct):" if reasons else "gateway refusals: none")
for r in reasons:
    print(f"  - {clean(r)[:300]}")
log = run / "agent" / "transcript.log"
lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
hits = [ln for ln in lines if any(w in ln.lower() for w in ("error", "refused", "not allowed", "deny by default"))]
print(f"CLI error lines ({len(hits)}; last 10, capped):")
for ln in hits[-10:]:
    print("  " + clean(ln)[:300])
PY
"${VS[@]}" review || true
echo
echo "Check: state completed with a report; spend calls > 0 and unmetered = 0; no gateway refusals (else report"
echo "them: each names the body key, tool type or anthropic-beta value to allowlist); the PR/branch on the sandbox."
echo "When satisfied:  ${VS[*]} review --ack      Report the output above; never include your keys."
