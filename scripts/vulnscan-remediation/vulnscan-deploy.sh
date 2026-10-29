#!/usr/bin/env bash
# Deploy an agentteams commit where the daily vulnscan job runs it (the pipx venv), then say whether the Docker
# images that version needs exist. Walkthrough: docs_src/vulnscan-automation-walkthrough.md, "Deploying a new
# version".
#
#   bash scripts/vulnscan-remediation/vulnscan-deploy.sh [REF] [--no-wait] [--skip-images] [--with-scripts]
#
#   REF             commit, tag or branch to deploy (default: origin/main, after a fetch)
#   --no-wait       refuse at once if a remediation attempt or a daily scan holds its lock (default: wait 30 min each)
#   --skip-images   do not rebuild the tools / agent images (the readiness check still lists what is missing)
#   --with-scripts  also re-run vulnscan-remediate-auto.sh --install from the same commit
#
# Steps: extract REF with git archive OUTSIDE any git work tree; 1/4 pipx install --force, holding the remediation
# lock (remediate.flock) and then the daily lock (daily.lock), so neither job starts on a venv being replaced;
# 2/4 tools image + tools-selftest; 3/4 the agent image + agent-selftest when agent mode is on; 4/4 readiness: the
# image tags the installed version needs and whether each exists (the tags follow the package version, so a version
# bump orphans the old images), `vulnscan ready --json` when the installed version has it (else
# vulnscan-ensure-docker.sh --no-start), halts, handoff status and schedule status.
# Nothing here signs, merges or pushes. Run it outside the daily job's window.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
REF="origin/main" wait=wait images=1 scripts=0
for a in "$@"; do
  case "$a" in
    --no-wait) wait=--no-wait ;;
    --skip-images) images=0 ;;
    --with-scripts) scripts=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) echo "unknown option $a"; exit 2 ;;
    *) REF="$a" ;;
  esac
done
STATE="${AGENTTEAMS_VULNSCAN_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agentteams/vulnscan}"
LEGACY_LOCK="$STATE/remediate.lock"   # the mkdir lock of earlier runner versions
if [[ -d "$LEGACY_LOCK" ]]; then
  holder="$(cat "$LEGACY_LOCK/pid" 2>/dev/null || true)"
  if [[ -z "$holder" ]] || kill -0 "$holder" 2>/dev/null; then
    echo "a remediation run of an earlier runner version may be active ($LEGACY_LOCK, pid ${holder:-unknown}); run this after it ends"
    exit 2
  fi
fi
docker info >/dev/null 2>&1 || { echo "Docker is not running"; exit 2; }
if [[ "$REF" == origin/* ]]; then git -C "$REPO" fetch -q origin; fi
SHA="$(git -C "$REPO" rev-parse --verify "$REF^{commit}")"
echo "== deploying $REF = $SHA"

SRC="$(mktemp -d "${TMPDIR:-/tmp}/agentteams-${SHA:0:8}.XXXXXX")/agentteams-${SHA:0:8}"
mkdir -p "$SRC"
git -C "$REPO" archive "$SHA" | tar -x -C "$SRC"
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 && { echo "refusing: $SRC is inside a git work tree"; exit 2; }

echo "== 1/4 pipx install (the daily job's interpreter)"
# Hold the remediation lock, then the daily lock (the order the remediation runner takes them: it holds its own and
# its self-tests take daily.lock), for the whole install. A job that starts meanwhile exits on its held lock instead
# of running out of a venv that is being replaced. The system python holds them, since the venv is what gets
# replaced.
mkdir -p "$STATE"
/usr/bin/python3 -I -c 'import fcntl, subprocess, sys, time
wait, locks, argv = sys.argv[1] != "--no-wait", sys.argv[2:4], sys.argv[4:]
held = []
for lock, what in zip(locks, ("a remediation attempt", "a daily scan")):
    f = open(lock, "a+")
    for i in range(60 if wait else 1):
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if i == 0: print(f"  {what} holds {lock}; " + ("waiting for it to finish" if wait else "refusing (--no-wait)"), flush=True)
            if wait: time.sleep(30)
    else:
        sys.exit(f"{what} still holds {lock}; not reinstalling under it")
    held.append(f)
sys.exit(subprocess.call(argv))' "$wait" "$STATE/remediate.flock" "$STATE/daily.lock" pipx install --force "$SRC"
PY="$HOME/.local/pipx/venvs/agentteams/bin/python"
VS=("$PY" -P -m agentteams.vulnscan)
"$PY" -P -c 'import importlib.metadata as m; print("agentteams", m.version("agentteams"))'
agent_on="$("$PY" -P -c 'from agentteams.vulnscan import daily, agent_run as ar
try: a = ar.agent_cfg(daily.load_config())
except Exception: a = {}
print(1 if a.get("enabled") else 0, a.get("framework") or "goose")' 2>/dev/null || echo "0 goose")"
read -r agent_enabled framework <<<"$agent_on"

if [[ "$images" == 1 ]]; then
  echo "== 2/4 tools image + tools-selftest"
  VULNSCAN_PY="$PY" bash "$SRC/scripts/vulnscan-tools-image.sh"
  if [[ "$agent_enabled" == 1 ]]; then
    echo "== 3/4 $framework agent image + agent-selftest"
    VULNSCAN_PY="$PY" FRAMEWORK="$framework" bash "$SRC/scripts/vulnscan-agent-image.sh" --source "$SRC"
    "$PY" -P - <<'EOF'
import json
from agentteams.vulnscan import paths
try:
    rec = json.loads((paths.state_dir() / "agent-selftest.json").read_text())
except (OSError, ValueError) as exc:
    rec = {"passed": False, "detail": f"no readable agent-selftest.json: {type(exc).__name__}"}
print(f"agent-selftest: {'PASSED' if rec.get('passed') else 'FAILED'}")
for k, v in sorted((rec.get("checks") or {}).items()):
    print(f"  {k}: {v}")
if not rec.get("passed"):
    print("detail:", json.dumps(rec.get("detail"))[:1500])
EOF
  else
    echo "== 3/4 agent image: skipped (agent mode is off)"
  fi
else
  echo "== 2/4, 3/4 images: skipped (--skip-images)"
fi

echo "== 4/4 readiness"
# The image tags the installed version needs, and whether each exists.
"$PY" -P - <<'EOF' || echo "  could not compute the image tags (is the vulnscan config readable?)"
import subprocess
from agentteams.vulnscan import daily, subproc_confine as sc, agent_run as ar
cfg = daily.load_config()
tags = [("tools", sc.image_tag(sc.settings(cfg)))]
a = ar.agent_cfg(cfg)
if a.get("enabled"):
    tags.append((f"agent ({a.get('framework') or 'goose'})", ar.image_tag(a)))
for what, tag in tags:
    try:
        found = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", tag], capture_output=True, text=True,
                               timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        found = False
    print(f"  {what} image {tag}: {'present' if found else 'MISSING (rebuild it, or re-run without --skip-images)'}")
EOF
if "${VS[@]}" ready --help >/dev/null 2>&1; then
  "${VS[@]}" ready --json || echo "  vulnscan ready: not ready (exit $?)"
else
  line="$(bash "$SRC/scripts/vulnscan-remediation/vulnscan-ensure-docker.sh" --no-start)" && rc=0 || rc=$?
  echo "  ensure-docker (--no-start): $line (exit $rc)"
fi
"${VS[@]}" halts || true
"${VS[@]}" handoff status || true
"${VS[@]}" schedule status || true

if [[ "$scripts" == 1 ]]; then
  echo "== remediation runner scripts (--with-scripts)"
  bash "$SRC/scripts/vulnscan-remediation/vulnscan-remediate-auto.sh" --install
else
  echo
  echo "The remediation runner's installed scripts were not touched; re-run with --with-scripts to install this"
  echo "commit's scripts/vulnscan-remediation/ as well."
fi
echo "A failed image pull (e.g. python:3.12-slim) is usually transient: re-run this script."
