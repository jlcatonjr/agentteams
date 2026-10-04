# Handoff → agentteams (and researchteam): a branch-lifecycle procedure for `@git-operations` and `@cleanup`

**From:** baseAgent (operator Jim) · **Date:** 2026-10-04 · **Toolchain:** agentteams `1.0.0rc7`
**To:**
- agentteams maintainers, who own the `git-operations` and `cleanup` archetype templates and their emitted
  references: §1–§3;
- researchteam maintainers, who own the managed `scripts/agentteams_autosync_gate.sh`: §4.

**Status:** a request with evidence, revised after an `@adversarial` review (§6).
**Action needed:**
- emit the missing `git-procedures.md` with a branch-lifecycle procedure;
- add a post-merge branch step and a branch-audit duty to the two templates;
- fix the evergreen autosync PR going stale.

**Evidence:**
- `docs/branch-audit-2026-10-04.md` and `.csv`, which were independently audited;
- the cleanup run in `references/security-decisions.log.csv` (rows `2026-10-04T21:40:36Z` and `…T21:45:06Z`).

---

## TL;DR

baseAgent had 67 branch refs (10 local and 57 remote), and 66 of them were already merged into `main`. The gap that
caused this is shared between the generated documentation and baseAgent's own notes:
- **The generated fenced content** for `@git-operations` has no merge or post-merge procedure. Its Output Contract
  never asks what became of the branch, and `@cleanup`'s scope covers files only.
- **baseAgent's user-editable `## Project-Specific Notes`** (`git-operations.agent.md`, "Git-first audit trail")
  supply the workflow actually used: push the feature branch, merge `--no-ff`, push `main`. They stop there.
- **Result:** no layer ever deletes a merged branch.
- **A dangling reference.** The fenced content also names `references/git-procedures.md` as "ground truth" (in
  `.github/agents/git-operations.agent.md`, and in `.codex/agents/git-operations.toml`). It resolves to
  `.github/agents/references/git-procedures.md`, which is not emitted and does not exist anywhere in this
  repository's history (`git log --all -- '*git-procedures*'` is empty).

baseAgent has cleaned up by hand once, with 0 failures, every step fail-closed and cleared by `@security`:
- deleted 9 local and 43 remote branches;
- pushed one release tag.

**Requested:** make the procedure below generated and durable for every team agentteams emits. baseAgent will add
the post-merge step to its own Project-Specific Notes as an interim measure, if the operator approves (§5).

## Evidence

| Category (2026-10-04, `origin/main` = `0fa444f`) | Refs | What baseAgent did |
|---|---:|---|
| Merged and owned by the operator: tip is an ancestor of main | 52 | Deleted. The 9 local ones used `git branch -d`; the 43 remote ones used `--force-with-lease` on each audited SHA. |
| Merged release branch (`release/report18-2026-10-02`) | (1 of the 52) | Tagged `report18-release` (annotated, on `6072cf1`), confirmed with `ls-remote`, then deleted |
| Merged branch checked out in a dead worktree (`agentteams/rc8-update`) | (1 of the 52) | `git worktree prune`, then `-d` |
| Merged branch owned by a collaborator (11 `agentic-capital-*`) | 11 | Kept, pending the owner's consent |
| Merged, with GitHub `tree/` links in correspondence already sent (`ac-harness-a12-v3`) | 1 | Kept until the tag exists and the links point at it |
| Not merged: evergreen autosync branch, open PR #9 | 1 | Kept (§4) |
| `main` and `origin/main` | 2 | Kept |

The same audit also found:
- a superseded stash;
- a stale worktree record;
- an untracked cache directory;
- the repository setting `delete_branch_on_merge: false`.

Most merges here are local `--no-ff` merges, not PRs, so that setting alone would not have helped.

## Requested changes (agentteams)

### 1. Emit `.github/agents/references/git-procedures.md`, containing a branch-lifecycle section

**1a. Branch states.** Classify every ref against the default branch after `git fetch --all --prune`. Act only on the
project's **push remote** (normally `origin`), never on an upstream or a fork remote.

| State | Test | Default action |
|---|---|---|
| **Default** | the default branch | Keep. Check that local equals the push remote. |
| **Protected** | branch protection or a ruleset covers it (API; `N/A` if unreachable) | Keep. Never delete. |
| **Active** | not an ancestor of the default branch; last commit within *N* days (default **N = 14**) | Keep |
| **Merged** | tip **is an ancestor** of the default branch (`git merge-base --is-ancestor <tip> <default>`) | Delete, unless a hold (§1b) applies |
| **Patch-equivalent** | not an ancestor, but `git cherry <default> <tip>` shows 0 unique patches (cherry-picked, rebased or squash-merged) | **Don't treat as merged.** Patch IDs ignore whitespace and skip merge commits, so a conflict resolution or a whitespace-significant change can hide. Tag it `archive/<branch>` and ask the operator. |
| **Stale-unmerged** | not an ancestor, has unique patches, no commits in *N* days | **Never auto-delete.** Report it to the operator with three choices: merge, rebase and continue, or archive under `archive/<branch>` and then delete. |
| **Evergreen / bot** | named by a workflow (for example `create-pull-request` `branch:`) or a gate script | Keep. Reconcile its PR; never delete. |
| **Release (finished)** | matches the project's release pattern and is merged, with no commits after its release | Tag (annotated), confirm on the remote, then delete |
| **Release (maintained)** | a release line that receives backports, or is declared maintained | Keep |

**Ancestry alone decides "Merged".** This keeps every deleted branch's commits reachable from the default branch:
- nothing can be lost to garbage collection;
- the branch can be restored from its recorded SHA, or from the second parent of its `--no-ff` merge commit.

**1b. Holds that override "Delete".** If any of these applies, keep the branch, and record the hold in the audit.
- **Owner.** Any author of the branch's own commits isn't on the operator list, where those commits are the ones
  not on the default branch's first-parent history. The operator list is a configured identity list, for example
  `references/security-approvers.txt` or a declared set of emails. Delete only with the owner's consent, batched as
  one question.
- **Link.** A URL to the branch (`tree/<branch>`, `blob/<branch>/`, `compare/…<branch>`) appears in a tracked file,
  **including correspondence drafts that have been sent**. Links already sent outside the repository can't be
  found by a scan, so a branch that was ever shared by link is tagged before deletion and its links repointed.
- **Open PR.** The branch is the head **or the base** of an open PR, including PRs from forks.
- **Worktree.** It is checked out in a live worktree. A dead worktree record is pruned first.
- **Pinned.** A workflow `ref:`, a submodule or a lockfile pins it.
- **Session.** Another active session (per the project's session-allocation file, if any) declares it as its
  working branch.

**1c. Safe deletion: every guard is required.**
1. **Re-check before acting.** Re-run the inventory just before acting, and stop on any drift from the audited tip
   SHAs.
2. **Local.** Check ancestry against the default branch explicitly. `git branch -d` checks against the upstream or
   `HEAD`, not the default branch, so it is a last guard, not the test. Use `-d`, **never** `-D`.
3. **Remote.** Delete one branch at a time:
   `git push <push-remote> --delete --force-with-lease=refs/heads/<b>:<audited-full-sha> <b>`.
   Re-check ancestry before each one. On any failure, stop that branch and report it. Never retry with force.
4. **Tags.**
   - Before creating a tag (releases, archives), check that it doesn't already exist, locally or on the remote.
   - Never move an existing tag.
   - Use annotated tags, and confirm them with `git ls-remote --tags` before deleting the branch.
5. **Clearance (C-5).** A bulk or remote deletion needs a recorded `@security` clearance before it runs. Record the
   verification of its conditions as a **new appended row**. Never edit a logged row.
6. **Record the run:** each deleted ref with its old full SHA. Restore with `git push <remote> <sha>:refs/heads/<b>`.

**1d. The inventory.** Ship an emitted script (for example `scripts/branch_inventory.sh`) that writes one CSV row per
ref, containing:
- ahead and behind counts (`rev-list --left-right --count`);
- whether the tip is an ancestor of the default branch;
- the `git cherry` unique-patch count, shown for information only;
- the tip SHA (full and short), every author of the branch's own commits, and the date of the last commit;
- the upstream and its tracking state;
- files citing the branch (`git grep -w -F <name> <default> -- . ':!<summaries dir>'`);
- protection and open-PR status (via the API, or `N/A`);
- the derived state from §1a and any holds from §1b.

### 2. `git-operations` template: a post-merge step and a session close-out check

- **A post-merge step** in the fenced content. After a merge to the default branch is pushed and the post-push run
  check passes, apply §1c to the merged branch in the same session: delete it locally and on the remote, unless a
  hold applies.
- **Merge message.** The `--no-ff` message names the branch, so the second parent records the tip and the branch
  can be restored.
- **Output Contract additions:**
  - `Branch disposition: deleted (local and remote, old SHA) | kept — hold: <reason>`;
  - `Branch inventory: clean | N merged-undeleted | N patch-equivalent | N stale-unmerged`.
- **Close-out.** When `@orchestrator` closes a multi-file session, `@git-operations` runs the §1d inventory and
  reports anything that isn't clean.

### 3. `cleanup` template: own the periodic branch audit

- **Scope:** add branches, tags (read-only, for collision checks), stashes and worktrees to `@cleanup`'s artifact
  scope.
- **Cadence:** run the §1d inventory weekly, or on request. Propose deletions by state; execution follows §1c,
  including clearance.
- **Stashes:** drop one only when its hand-written content is identical to the default branch and the rest is
  regenerable output. Show the check.
- **Worktrees:** `git worktree prune` for records whose directory no longer exists.
- **Multiple machines:** the audit covers only the machine it runs on and the push remote. Say so, and ask the
  operator to run it on every machine with local work.

**Acceptance (agentteams):**
- **Emitted files:** a fresh `agentteams --update --merge` emits `.github/agents/references/git-procedures.md`
  containing §1a–§1d, and the Codex and Goose copies cite it correctly.
- **Template changes:** the `git-operations` Output Contract carries the two new fields, and `cleanup` lists
  branches, stashes and worktrees.
- **Fixture tests.** The inventory classifies each of these correctly:
  - a merged branch;
  - a squash-merged branch (patch-equivalent);
  - an "evil merge" or whitespace-only patch (not merged);
  - a stale-unmerged branch;
  - a branch held by an open PR, by a link, or by a collaborator;
  - a branch whose tip moves between audit and deletion (the lease fails closed).

## 4. Requested change (researchteam): the evergreen autosync PR goes stale and conflicts

**Background.** `.github/workflows/agentteams-sync.yml` delegates to `scripts/agentteams_autosync_gate.sh`, which
researchteam manages. The gate keys on the agentteams SHA, so timestamp-only regenerations correctly open no PR.

**Observed.** PR #9 (branch `chore/agentteams-autosync`) holds one commit, `200ef03`, from 28 September, built on
`63d038f`:
- the autosync-ref moves from `f3a8b8c` to `3c06a93`;
- a threat-intel refresh in which only the timestamps changed (`security-vulnerability-watch.json` and the fenced
  snapshot in `security.agent.md`);
- a new manifest fingerprint and a new `memory-index.vcache` key.

Since then main has changed `memory-index.vcache` (`528a927`), so the PR now **conflicts**.
- **Why it stays stale:** the SHA-gate compares against the ref recorded on the evergreen branch. While the
  agentteams SHA doesn't move, no run changes anything (the 21 September run failed), so the branch is never
  rebuilt.
- **Its intel also ages.** That matters, because a fresh intel snapshot is what clears the intel-freshness gate on
  `agentteams --adopt-orphans --overwrite`.

**Requested:**
1. **Rebuild or flag on base drift.** When the default branch has moved and the open evergreen PR no longer merges
   cleanly, rebuild the branch on the current base even if the SHA is unchanged, or at least label or comment on
   the PR.
2. **Diagnose the dropped hashes.** The CI-regenerated `build-log.json` on the PR branch **drops** two `file_hashes`
   entries that main's locally built log records: `../../sandbox/confine-run.sh` and `../../.vscode/tasks.json`.
   Merging the PR would silently stop drift-checking the sandbox launcher. Please find the cause; a difference
   between the local and CI emit configurations is plausible. Until then, flag any PR that removes hash entries.
3. **Record:** 3 of the last 5 runs failed (2 Sep dispatch, 7 Sep and 21 Sep scheduled). Check that the failures
   aren't what keeps the branch stale.

## 5. What baseAgent will and won't do

- **Won't hand-edit generated fenced content.** The next sync would discard it.
- **Interim measure, with the operator's approval:** add a short post-merge step pointing at
  `docs/branch-audit-2026-10-04.md` to the user-editable `## Project-Specific Notes` of `git-operations.agent.md`.
  That is project-owned text, where baseAgent's merge workflow already lives. The procedure in this handoff applies
  meanwhile.
- **PR #9:** baseAgent won't merge it as it stands. The plan is to refresh it via `force=true`, or close it so the
  next real SHA change reopens it, and to review the hash-entry drop before merging.

## 6. Revision record

An `@adversarial` review of the first draft returned 5 blocking and 6 advisory findings. All are reflected above:

| Draft claim or rule | Revision |
|---|---|
| "Merged = ancestor **or** 0 cherry patches" | Merged = ancestor only; added a patch-equivalent state |
| The workflow gap was attributed to the generated content alone | The gap is shared with baseAgent's Notes; interim fix in §5 |
| Autosync requests sent to agentteams | Sent to researchteam (the gate script is researchteam-managed) |
| "Suppress timestamp-only PRs" | Dropped: the SHA-gate already does this, and intel refreshes are needed |
| "Rebuild the branch on each run" | Replaced with: rebuild or flag when the base drifts while the SHA is unchanged |
| A fix was prescribed for the dropped hashes | Now a request to diagnose; the drop was confirmed in the PR diff |
| Missing coverage | Added protected branches, maintained release lines, tag collisions, fork PRs, the push remote only, restore, *N*, the operator list, the `-d` semantics and fixture tests |
| The log row was edited in place | baseAgent's log now carries a separate appended verification row |
