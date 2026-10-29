#!/usr/bin/env bash
# Give every vulnscan target repo a goose orchestrator that accepts remediation handoffs.
# Standard model for all remediation work: goose + OpenRouter + z-ai/glm-5.3 (via your goose route proxy).
#
#   bash scripts/vulnscan-remediation/vulnscan-target-agents.sh [--yes]
#
# For each repo under VULNSCAN_REPOS_ROOT whose origin is owned by VULNSCAN_GITHUB_ORG (vulnscan-handoff-lib.sh)
# with fixable findings in the latest vulnscan run:
#   - It already has a goose team (.goose/recipes/orchestrator.yaml, in the checkout or in its team worktree):
#     make sure the vulnscan manager team ($MANAGER_TEAM) is on its roster (asks).
#   - It has none: create a worktree <repo>.worktrees/goose-agent-team on a new branch
#     chore/goose-agent-team-<date> from origin's default branch (your checkout and its uncommitted work are not
#     touched), add $MANAGER_TEAM to the brief there (drafting a brief from the README if the repo has none),
#     and render a goose team with agentteams (--framework goose --merge). The roster comes with the render.
#     About 75 new files (.goose/recipes/, AGENTS.md, .goosehints, sandbox/, scripts/goose-*.py): review,
#     commit and open one PR per repo from that worktree.
# Existing Claude Code / Copilot agent files in the repos are left as they are; the workflow no longer uses them.
# --yes = auto mode (no prompts; each approval logged to operator-clearances.log). The scheduled job uses it so a
# repo that newly has findings gets its goose team without a manual step.
# Once, first: the management-directive signing key (asks; 0600 file, never exported). Nothing is pushed.

set -euo pipefail
if [[ "${1:-}" == --yes ]]; then export VULNSCAN_AUTO=1; shift; fi   # auto mode: see vulnscan-handoff-lib.sh
. "$(dirname "$0")/vulnscan-handoff-lib.sh"
LOG="$STATE_DIR/target-agents-$(date +%Y%m%dT%H%M%S).log"
mkdir -p "$STATE_DIR"
exec > >(tee -a "$LOG") 2>&1
[[ -t 0 || "$AUTO" == 1 ]] || { echo "run this from an interactive terminal, or pass --yes for auto mode"; exit 2; }
[[ -x "$AT" ]] || { echo "missing $AT"; exit 2; }
echo "Standard agent runtime: goose --provider $GOOSE_PROVIDER_STD --model $GOOSE_MODEL_STD"

say "Management-directive signing key ($MGMT_KEYFILE)"
if [[ -f "$MGMT_KEYFILE" ]]; then
  ok "present"
elif ask "No key yet. Generate a 256-bit key there (0600; it signs and verifies handoff directives)?"; then
  mkdir -p "$(dirname "$MGMT_KEYFILE")"; chmod 700 "$(dirname "$MGMT_KEYFILE")"
  ( umask 077; openssl rand -hex 32 > "$MGMT_KEYFILE" )
  ok "generated (never put it in a shell profile or an agent session)"
else
  warn "without it, handoffs still run but carry no signed directive"
fi

latest="$(latest_run)"
[[ -f "$latest/summary.json" ]] || { echo "no completed vulnscan run under $STATE_DIR"; exit 2; }
repos=()
while IFS= read -r line; do [[ -n "$line" ]] && repos+=("$line"); done < <(fix_repos "$latest")

add_manager_to_brief() {   # $1 = brief path
  "$PY" -P - "$1" "$MANAGER_TEAM" <<'EOF'
import json, sys
p, m = sys.argv[1], sys.argv[2]
d = json.load(open(p))
mgrs = [x for x in (d.get("authorized_managers") or []) if isinstance(x, str)]
if m.lower() not in [x.lower() for x in mgrs]:
    d["authorized_managers"] = mgrs + [m]
    open(p, "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    print(f"  added {m} to authorized_managers in {p}")
EOF
}

draft_brief() {   # $1 = dir to scan for a README, $2 = output path
  "$PY" -P - "$1" "$2" "$MANAGER_TEAM" <<'EOF'
import json, re, sys
from pathlib import Path
repo, out, manager = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
goal = ""
readme = next((p for p in (repo / "README.md", repo / "readme.md", repo / "README.rst") if p.exists()), None)
if readme:
    paras = [p.strip() for p in re.split(r"\n\s*\n", readme.read_text(errors="replace"))]
    paras = [p for p in paras if p and not p.startswith(("#", "```", "**", "![", "[", "|", "-", "*", "<"))]
    goal = re.sub(r"\s+", " ", paras[0])[:600] if paras else ""
name = re.sub(r"[^A-Za-z0-9_.-]", "-", repo.name.replace(".worktrees", ""))
brief = {"project_name": name,
         "project_goal": goal or f"{name}: (edit this goal before generating).",
         "authorized_managers": [manager]}
out.write_text(json.dumps(brief, indent=2) + "\n")
print(json.dumps(brief, indent=2))
EOF
}

create_goose_team() {   # $1 = repo
  local repo="$1" name wt base branch brief
  name="$(basename "$repo")"; wt="$(team_worktree "$repo")"
  echo "  no goose orchestrator yet"
  ask "Create worktree ${wt#"$REPOS_ROOT"/} on a new branch and render a goose team for $name there?" || return 1
  git -C "$repo" fetch -q origin
  base="$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ -z "$base" ]]; then
    for b in origin/main origin/master; do git -C "$repo" rev-parse -q --verify "$b" >/dev/null && { base="$b"; break; }; done
  fi
  [[ -n "$base" ]] || { warn "cannot find origin's default branch for $name"; return 1; }
  branch="$TEAM_BRANCH_PREFIX-$(date +%Y%m%d)"
  if [[ -d "$wt" ]]; then
    warn "${wt#"$REPOS_ROOT"/} already exists without a goose team; using it as is ($(repo_state "$wt"))"
  else
    git -C "$repo" worktree add -q -b "$branch" "$wt" "$base"
    ok "worktree ${wt#"$REPOS_ROOT"/} on $branch (from $base)"
  fi
  brief="$(brief_of "$wt")"
  if [[ -z "$brief" ]]; then
    brief="$wt/brief.json"
    draft_brief "$wt" "$brief"
    echo "  drafted ${brief#"$REPOS_ROOT"/} (edit it now if the goal is wrong)"
    [[ "$AUTO" == 1 ]] || read -r -p "  Press Enter to continue once the brief is right... " _ </dev/tty
  else
    echo "  brief: ${brief#"$REPOS_ROOT"/}"
    ask "Add '$MANAGER_TEAM' to its authorized_managers (privileged roster change, on the new branch only)?" || return 1
    add_manager_to_brief "$brief"
  fi
  "$AT" --description "$brief" --project "$wt" --framework goose --merge --yes
  [[ -f "$wt/.goose/recipes/orchestrator.yaml" ]] || { warn "render did not produce .goose/recipes/orchestrator.yaml"; return 1; }
  ok "goose team rendered in ${wt#"$REPOS_ROOT"/} (uncommitted): review, commit and open a PR from that worktree"
}

enrol_manager() {   # $1 = home (checkout or team worktree), $2 = recipes dir
  local home="$1" tdir="$2" brief
  brief="$(brief_of "$home")"
  echo "  roster: ${tdir#"$home"/}/references/authorized-managers.txt   ($(repo_state "$home"))"
  ask "Enrol '$MANAGER_TEAM' as an authorized manager there (privileged roster change; left uncommitted)?" || return 1
  mkdir -p "$tdir/references"
  printf '%s\n' "$MANAGER_TEAM" >> "$tdir/references/authorized-managers.txt"
  [[ -n "$brief" ]] && add_manager_to_brief "$brief"   # keep the brief in step, or the next --update drops it
  ok "enrolled"
}

summary=()
for repo in "${repos[@]}"; do
  name="$(basename "$repo")"
  say "$name"
  orch="$(orchestrator_of "$repo")"
  if [[ -z "$orch" ]]; then
    create_goose_team "$repo" || true
    orch="$(orchestrator_of "$repo")"
    [[ -n "$orch" ]] || { summary+=("$name: NO GOOSE ORCHESTRATOR (handoff not possible yet)"); continue; }
  fi
  IFS='|' read -r _ home tdir <<<"$orch"
  ok "goose orchestrator: ${tdir#"$REPOS_ROOT"/}/orchestrator.yaml"
  if manager_rostered "$tdir"; then
    ok "'$MANAGER_TEAM' on the roster"; summary+=("$name: ready (${home#"$REPOS_ROOT"/})")
  elif enrol_manager "$home" "$tdir"; then
    summary+=("$name: ready (${home#"$REPOS_ROOT"/}; roster edit uncommitted)")
  else
    summary+=("$name: goose orchestrator; '$MANAGER_TEAM' not rostered (handoff runs, directive skipped)")
  fi
done

say "Summary"
printf '  %s\n' "${summary[@]}"
echo; echo "Log: $LOG"
