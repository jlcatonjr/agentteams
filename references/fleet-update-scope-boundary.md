# Fleet-Update Scope Boundary Documentation

**Date:** 2026-05-08  
**Author:** Orchestrator (fleet-update-all-repositories)  
**Scope Authority:** Orchestrator Authority Hierarchy; Build-Team-Plan; Adjacent-Repos.md  

---

## I. Scope Definition

### Included Repositories

**Scope Criteria:** All repositories under `~/githubrepositories/` that contain a valid `_build-description.json` file at the project root or within a `.github/agents/` directory.

**Total in Scope:** ~38 canonical repositories (the literal count drifts as repos are added; do not treat "38" as a fixed gate — see the Discovery Stability Check in §III).  
**Discovery Method:** `find ~/githubrepositories -type f -name '_build-description.json' -not -path '*/.worktrees/*' -not -path '*/*.worktrees/*' -not -path '*/archive/*' | sort`  
**Discovery Date:** 2026-05-08 (re-run 2026-06-04)

> **Exclude worktree and archive copies.** A bare `find … -name '_build-description.json'` also matches `.worktrees/copilot-worktree-*` git-worktree copies and `archive/<repo>` snapshots, which inflated the 2026-06-04 discovery to ~40 paths. These are duplicates of an in-scope repo (a worktree shares the repo's history; an archive copy is out of maintenance) and fall under the existing exclusions in §V (items 1 & 4). The `-not -path` filters above drop them so the count reflects canonical repos only.

**Scope Categories:**

| Category | Count | Examples | Justification |
|---|---|---|---|
| Primary Framework Targets | 4 | copilot-vscode, copilot-cli, claude-agent, agentteams | Framework interoperability; bridge infrastructure |
| Major Software Projects | 8 | VisualKnowledge, smart-contracts, llm-training, distributed-systems | Production deployments; agent team scaffolding |
| Data & Research Projects | 4 | learn-python-for-stats-and-econ, data-pipeline, research-project-1 | Educational and analytical; agent-assisted workflows |
| Secondary/Exploratory | 22 | (various small project repos) | Proof-of-concepts, forks, experimental branches |
| **TOTAL** | **38** | — | All have valid `_build-description.json` and agent infrastructure |

### Explicitly Excluded Repositories

**Scope Criteria:** Repositories under `~/githubrepositories/` that do **NOT** contain `_build-description.json`.

**Total Excluded:** 31 repositories  
**Exclusion Reason:** No agent team descriptor available; update would fail with "descriptor not found" error. Excluded repos are not invalid—they simply have not yet adopted agent team infrastructure.

**Exclusion List (sample):**

| Repo Path | Reason | Update Path |
|---|---|---|
| `~/githubrepositories/legacy-monolithic-app` | No `_build-description.json` present | Manual agent team setup required first |
| `~/githubrepositories/fork-of-external-lib` | Mirror repo; no descriptor | Explicitly excluded to avoid drift with upstream |
| `~/githubrepositories/archived-2023` | Archived project; no descriptor | Out of maintenance; no updates planned |
| (27 others) | Various (unmaintained, pre-agent-team adoption, vendor code) | Clarification: Full list stored in `tmp/by-week/2026-W19/fleet-update-scope-clarification.txt` |

---

## II. Scope Boundaries & Authority

### Boundary Definitions

**Primary Boundary:** Agent team adoption via `_build-description.json`
- Repos with descriptor → **In scope**
- Repos without descriptor → **Excluded**
- Repos with descriptor in non-standard path → **Out of scope** (requires manual discovery and path normalization)

**Secondary Boundary:** Directory authorization
- Only repositories within `~/githubrepositories/` are in scope
- Repositories in other parent directories (e.g., `~/personal-projects/`) are explicitly excluded
- Cross-user repositories (under another user's home, e.g. `/Users/<other-user>/...`) are explicitly excluded

**Tertiary Boundary:** Update mode constraints
- All in-scope repos use `--update --merge --yes` mode (non-destructive, section-fence preservation)
- No bare `--update` (destructive mode) without explicit operator approval
- No `--dry-run` in fleet loop (dry-run performed separately as pre-check)

### Authority & Approval

**Approval Authority for Fleet-Wide Scope:** Orchestrator (defined in AgentTeamsModule orchestrator.agent.md)

**Approval Authority for Individual Repo Exceptions:** @repo-liaison (Workflow 9 Protocol 3 for repos with own orchestrator)

**Scope Extension Requests:** Any request to include repositories outside the defined boundary (new directory, new parent folder, non-standard descriptor path) requires:
1. Documented justification in `references/fleet-update-authorization-policy.md`
2. Security review via `@security` → Infrastructure Exception Pathway
3. Operator approval before plan execution

**Scope Reduction Requests:** Any request to exclude a repo that currently has a valid descriptor requires:
1. Documented reason in `references/adjacent-repos.md` (new row, update_scope → excluded)
2. Confirmation from repo owner or maintainer
3. Notification in next fleet summary report

---

## III. Scope Validation & Audit

### Pre-Execution Validation

Before Step 4 (Execute check/update loop) proceeds:

1. **Discovery Stability Check:**
   - Re-run find command immediately before execution
   - Verify count matches original discovery (38 repos)
   - If count differs:
     - Log warning to `tmp/by-week/2026-W19/fleet-update-discovery-drift.txt`
     - Either: (a) proceed with current set (new repos not included), or (b) abort and re-run discovery

2. **Scope Clarity Check:**
   - Output two lists to `tmp/by-week/2026-W19/fleet-update-scope-clarification.txt`:
     - Repos expected to be excluded (list of 31 repos without descriptors)
     - Repos expected to be updated (list of 38 repos with descriptors)
   - Operator must confirm before proceeding

3. **Boundary Compliance Check:**
   - Scan all 38 repos for non-standard descriptor paths
   - Flag any repos where descriptor is not at project root or `.github/agents/_build-description.json`
   - Log findings to `tmp/by-week/2026-W19/fleet-update-path-anomalies.csv`

### Post-Execution Validation

After Step 5 (Validate outcomes) completes:

1. **Scope Completion Check:**
   - Results CSV must have exactly 38 rows (one per in-scope repo)
   - If count differs from 38:
     - Investigate missing/extra repos
     - Update `references/adjacent-repos.md` with new findings
     - Notify user and route to `@repo-liaison` for registry maintenance

2. **Scope Drift Detection:**
   - Check if any repos were added/removed from filesystem during execution
   - Compare results CSV against discovery list
   - Flag any repos in discovery list that don't appear in results (execution failure vs. scope drift)

---

## IV. Relationship to Other Governance Documents

### Relationship to Build-Team-Plan

The original `build-team-plan.md` defines scope as "a single project" (agentteams repository). This fleet-update extends that scope to 38 repositories. The extension is authorized by:

1. **Orchestrator Authority:** Orchestrator has delegation to execute cross-repo operations (Workflow 9)
2. **Infrastructure Exception Pathway:** Fleet-update is a documented exception to single-project scope
3. **Adjacent-Repos Registry:** All 38 repos are now registered in canonical registry

**Scope Extension Rationale:** AgentTeamsModule is a shared framework across multiple projects. Agent team infrastructure maintenance requires periodic synchronization across all consuming projects. Fleet-update is the canonical mechanism for this synchronization.

### Relationship to Adjacent-Repos.md

This document defines **which repos are included/excluded**. Adjacent-repos.md defines **governance attributes** (approval gates, orchestrator presence, update scope categories).

Together:
- This doc (scope-boundary.md) answers: "Should repo X be updated?" (yes/no based on descriptor presence)
- Adjacent-repos.md answers: "How should repo X be updated?" (approval gate, update_scope, orchestrator protocol)

### Relationship to Fleet-Update Authorization Policy (pending)

When created, that policy will define:
- Who can authorize fleet-wide operations
- Approval chain and sign-off gates
- What constitutes valid scope extension
- Rollback authority and procedures

---

## V. Out-of-Scope Examples

The following scenarios are **explicitly out of scope** for this fleet-update:

1. **Descriptor in non-standard path:** Repo has `_build-description.json` in a custom path (e.g., `src/agents/_build-description.json`) instead of project root or `.github/agents/`. → Excluded; requires manual path normalization in future.

2. **External dependencies:** Repo cloned from external source (upstream mirror) with its own descriptor. → Excluded for this fleet; hand off to `@repo-liaison` Protocol 3 (Orchestrator-to-Orchestrator).

3. **Vendor code with agent infrastructure:** Repo contains vendor code but has been modified with agent team infrastructure. → Included IF descriptor is present; update_scope in adjacent-repos.md is "security_only" or "manual_review" to prevent inadvertent modifications.

4. **Repositories in other parent directories:** E.g., `~/archive/old-projects/some-repo/`. → Explicitly excluded; parent directory boundary is `~/githubrepositories/` only.

5. **Cross-user repositories:** E.g., another user's home such as `/Users/<other-collaborator>/githubrepositories/shared-project/`. → Explicitly excluded; cross-user coordination requires explicit approval and `@repo-liaison` handoff.

---

## VI. Rollback & Scope Recovery

If the fleet-update execution reveals scope issues (e.g., discovered repo cannot be updated, scope was too broad, new repos appeared mid-execution):

1. **For repos that succeeded but should not have:** No rollback needed; `--merge` mode preserves user content. Run `git diff` to review changes; commit only if intentional.

2. **For repos that failed:** Backups exist (stored in each repo's `.github/agents/.agentteams-backups/<timestamp>/` — the auto-backup written under the output dir, not a top-level `.backups/`). Restore by copying the backed-up files back, or `git restore` if the repo is tracked. Note: a non-zero exit is usually a post-merge attestation crash over a successful merge — confirm with a content audit before treating a repo as "failed" (see [Systematic Update Lessons](systematic-update-lessons.md)).

3. **For scope drift (repos added/removed during execution):** Update `references/adjacent-repos.md` and `tmp/by-week/2026-W19/fleet-update-scope-clarification.txt`. Re-run fleet-update in next cycle with corrected scope.

---

## VII. Future Scope Changes

**Adding new repositories to scope:**
1. Create `_build-description.json` in target repo
2. Add entry to `references/adjacent-repos.md`
3. Include in next fleet-update cycle

**Removing repositories from scope:**
1. Delete or move `_build-description.json` to non-standard path
2. Update `references/adjacent-repos.md` (update_scope → excluded, add rationale)
3. Notify in next fleet summary report

---

## VIII. Scope Sign-Off

**Scope Approval Authority:** Orchestrator (AgentTeamsModule)  
**Scope Authority Date:** 2026-05-08 (re-validated 2026-06-04)  
**Scope Stability Expiration:** 2026-06-25 (21-day review cycle from the 2026-06-04 re-validation)  

This scope boundary is valid for fleet-update execution as of the 2026-06-04 re-validation. On or after **2026-06-25** (the Scope Stability Expiration in §VIII, matching the Next Review date in `references/fleet-update-authorization-policy.md`), scope must be re-audited and re-approved before proceeding with the next fleet-update cycle.

---

**Document Version:** 1.0 (Fleet-Update-All-Repositories, 2026-W19)  
**Last Updated:** 2026-06-04  
**Maintenance:** `@repo-liaison` Protocol 4 after each fleet cycle
