#!/usr/bin/env bash
# 10 — live acceptance of the tools container (baseAgent hardening plan W1). Run in your own interactive
# terminal, after 01-03 and 02 (the sandbox repo), with Docker running.
#
#   1. builds the tools image (scripts/vulnscan-tools-image.sh: Node.js pin checked against the signed
#      SHASUMS256.txt, base image by digest) and runs tools-selftest; prints every check
#   2. checks vulnscan.json does not opt out (confine.subprocesses must not be false)
#   3. a confined npm reachability probe (`npm ping` through the egress proxy: registry.npmjs.org only)
#   4. signs a NARROW grant if none is active: stage-fix + open-pr, only ~/vulnscan-sandbox-root, 1 day, 4 uses
#      (you review every field and confirm with y; your key is named on this one command only)
#   5. ONE stage-fix of the sandbox repo through the executor (every git command in the tools container);
#      checks the staged worktree is owned by you
#   6. open-pr DRY CHECK: from inside the container, a credentialed `git ls-remote origin` (token on stdin,
#      egress to origin's host only) and the gh repository the PR would target; then asks whether to open
#      the PR for real (default: no)
# It never prints a token: the token goes from `gh auth token` to the container's stdin inside agentteams.
#
# Usage:   KEY_ID=op1 [NAME=vulnscan-sandbox] bash scripts/vulnscan-operator/10-accept-tools-container.sh
set -euo pipefail

KEY_ID="${KEY_ID:-op1}"
NAME="${NAME:-vulnscan-sandbox}"
ROOT="$HOME/vulnscan-sandbox-root"
REPO="$ROOT/$NAME"
GOV="$HOME/ops-team/.goose/recipes"
PRIV="$HOME/.config/agentteams/keys/decision-signing-$KEY_ID.pem"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)/agentteams/bin/python"
[ -x "$PY" ] || { echo "run 01-install.sh first" >&2; exit 2; }
VS=("$PY" -P -m agentteams.vulnscan)
[ -t 0 ] || { echo "run this in an interactive terminal (signing asks you to confirm)" >&2; exit 2; }
[ -f "$PRIV" ] || { echo "no operator key $PRIV (run 03-configure.sh)" >&2; exit 2; }
[ -d "$REPO/.git" ] || { echo "no sandbox repo $REPO (run 02-sandbox-repo.sh)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 2; }

echo "== 1/6 tools image and tools-selftest"
VULNSCAN_PY="$PY" bash "$HERE/../vulnscan-tools-image.sh" || true
"$PY" -P - <<'PY'
import json, sys
from agentteams.vulnscan import daily, subproc_confine as sc
f = sc.selftest_record_path()
rec = json.loads(f.read_text()) if f.is_file() else {}
print(f"tools-selftest: {'PASSED' if rec.get('passed') else 'FAILED'} (image {rec.get('image')})")
for k, v in sorted((rec.get("checks") or {}).items()):
    print(f"  {'ok  ' if v else 'FAIL'} {str(k)[:80]}")
why = sc.unavailable_reason(sc.image_tag(sc.settings(daily.load_config())))
print("binding:", why or "current image and flags")
sys.exit(1 if why else 0)
PY

echo "== 2/6 confinement is on"
"$PY" -P -c 'import sys; from agentteams.vulnscan import daily, subproc_confine as sc
cfg = daily.load_config(); on = sc.enabled(cfg)
print("confine.subprocesses:", "on" if on else "OFF (opt-out)"); sys.exit(0 if on else 1)' \
  || { echo "vulnscan.json opts out (confine.subprocesses: false): remove that to accept the container" >&2; exit 1; }

echo "== 3/6 confined npm probe (egress: registry.npmjs.org:443 only)"
"$PY" -P - <<'PY'
import tempfile
from pathlib import Path
from agentteams.vulnscan import daily, subproc_confine as sc
with sc.scope(daily.load_config()):
    with tempfile.TemporaryDirectory(dir=sc.scratch_parent()) as tmp:
        p = sc.run_npm(["ping", "--registry=https://registry.npmjs.org/"], workdir=Path(tmp), scratch_root=Path(tmp),
                       env={}, timeout=120)
print(f"npm ping exit {p.returncode}: {(p.stdout + p.stderr).strip().splitlines()[-1:] or ''}")
PY

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/vulnscan-accept.XXXXXX")"
trap 'rmdir "$WORKDIR" 2>/dev/null || true' EXIT
cd "$WORKDIR"   # sign from an empty directory (never a repository, the team dir or $HOME)

echo "== 4/6 the narrow grant: stage-fix + open-pr on $ROOT, 1 day, 4 uses (review every field, then answer y)"
if "${VS[@]}" grant list 2>/dev/null | grep -q '"stage-fix"'; then
  echo "an active grant already exists:"; "${VS[@]}" grant list
else
  AGENTTEAMS_DECISION_ED25519_KEYFILE="$PRIV" "${VS[@]}" grant sign --key-id "$KEY_ID" --days 1 --max-uses 4 \
    --op stage-fix --op open-pr --repo-root "$ROOT" --governance-dir "$GOV"
fi

echo "== 5/6 stage-fix through the executor (git in the tools container)"
set +e
OUT="$("${VS[@]}" act stage-fix "$REPO" --rationale "W1 acceptance: confined stage-fix")"; rc=$?
set -e
echo "$OUT"
echo "act exit $rc (exit codes: 0 done, 1 refused, 3 failed)"
[ "$rc" -eq 0 ] || { echo "stage-fix did not complete: report the detail above (a refusal naming the tools container is the fail-closed path)" >&2; exit 1; }
WT="$("$PY" -P -c 'import json, sys; print(json.loads(sys.stdin.read()).get("worktree", ""))' <<<"$OUT")"
if [ -n "$WT" ] && [ -d "$WT" ]; then
  owner="$(stat -f %u "$WT/requirements.txt" 2>/dev/null || stat -c %u "$WT/requirements.txt")"
  [ "$owner" = "$(id -u)" ] && echo "staged worktree $WT: files owned by you (uid $owner)" \
    || { echo "staged files are owned by uid $owner, not you" >&2; exit 1; }
fi

echo "== 6/6 open-pr dry check (credentialed ls-remote in the container; nothing is pushed)"
"$PY" -P - "$REPO" <<'PY'
import sys
from pathlib import Path
from agentteams.vulnscan import daily, executor, subproc_confine as sc
repo = Path(sys.argv[1])
with sc.scope(daily.load_config(), scratch=[executor._worktrees_dir()]):
    proc = executor.git_proc(repo, "ls-remote", "origin", "HEAD", push=True)
    target = executor._origin_repo(repo)
print(f"confined ls-remote origin HEAD: exit {proc.returncode}" + ("" if proc.returncode else " (token handed over, egress to origin only)"))
print(f"PR would target: {target}")
sys.exit(proc.returncode)
PY
read -r -p "Open the PR for real now (pushes vulnscan/* to $NAME, opens a PR there)? [y/N] " ANS
case "$ANS" in
  y|Y) set +e; "${VS[@]}" act open-pr "$REPO" --rationale "W1 acceptance: confined open-pr"; rc=$?; set -e
       echo "open-pr exit $rc";;
  *) echo "skipped; the staged branch stays local (re-run act open-pr later, or remove its worktree)";;
esac
echo
echo "Report: the tools-selftest checks, the act results above and the review headline; never include a token."
echo "Then: ${VS[*]} review   (and --ack when satisfied)"
