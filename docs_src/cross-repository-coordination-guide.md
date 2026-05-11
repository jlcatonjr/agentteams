# Cross-Repository Coordination

## When to Use This Guide

Read this guide if you:

- Need to update agent infrastructure in more than one repository after an AgentTeams template change
- Are using `@repo-liaison` and want to understand the three protocols
- Manage `references/adjacent-repos.md` and need to know how to add, update, or audit entries
- Want to run fleet-wide updates across all repositories in your registry

For security clearance requirements when writing to adjacent repositories, see the [Security Hardening Guide](security-hardening-guide.md).

---

## The Adjacent Repository Registry

`references/adjacent-repos.md` is the authoritative registry of all repositories that may be affected by cross-repository operations. It is used by generated orchestrator governance workflows (especially Workflow 9) as the coordination source of truth.

### Registry Entry Format

| Field | Description |
|---|---|
| `repo_path` | Absolute filesystem path to the repository root |
| `agent_infra_path` | Relative path to agent infrastructure directory (e.g. `.github/agents/`) |
| `relationship` | `primary`, `secondary`, `mirror`, `vendor`, `fork`, or `archive` |
| `orchestrator_present` | Boolean — whether the repo has its own `orchestrator.agent.md` |
| `update_scope` | `full_update`, `security_only`, `manual_review`, or `excluded` |
| `approval_gate` | Agent responsible for approving updates (`orchestrator`, `repo-liaison`, `repo_owner`) |
| `last_audit_date` | ISO date of last scope audit |
| `notes` | Governance or special-handling notes |

### Update Scope Values

| Scope | Meaning |
|---|---|
| `full_update` | `--update --merge --yes` may be run without per-repo confirmation |
| `security_only` | Only security-related template sections may be updated |
| `manual_review` | Every proposed change must be reviewed and explicitly approved |
| `excluded` | Repository is out of scope for all automated updates |

---

## The Three `@repo-liaison` Protocols

### Protocol 1 — Impact Assessment

**When:** Before any action that might modify files outside this project's output directory.

The liaison assesses:
1. Which adjacent repos are potentially affected
2. What their current `update_scope` and `approval_gate` values are
3. Whether an orchestrator is present in each repo (affects coordination path)

Output: an Impact Report listing each affected repo with its scope and required approval path.

### Protocol 2 — Update Adjacent Repo Docs

**When:** Protocol 1 returned one or more approved repos for updates.

The liaison:
1. For each approved repo, proposes specific changes to the agent infrastructure files
2. Routes each write through `@security` for clearance (required by Constitutional Rule #11)
3. Executes the write only after clearance is confirmed and conditions in `references/security-decisions.log.csv` are verified
4. Records a changelog entry in `references/adjacent-repos-changelog.csv` for each completed update

### Protocol 3 — Orchestrator-to-Orchestrator Coordination

**When:** An adjacent repo has its own `orchestrator.agent.md` (i.e. `orchestrator_present = true`).

Direct writes to the adjacent orchestrator's governance files are not permitted without the other orchestrator's review. The liaison:
1. Drafts a Coordination Request — a structured summary of the proposed change, rationale, and affected files
2. Surfaces the Coordination Request to the user for approval before proceeding
3. If the adjacent orchestrator context is available in the session, presents the request for routing review there as well

---

## Fleet Updates

A fleet update runs `--update --merge` against every `full_update`-scoped repository in the registry in a single coordinated session.

### Pre-Flight

1. Read `references/adjacent-repos.md` — identify all `full_update` entries
2. For each repo, confirm `agent_infra_path` exists on disk
3. Run `--check` against each repo to identify which are stale (skip fresh repos to reduce write surface)

### Execution (per stale repo)

```bash
agentteams \
  --description <repo>/brief.json \
  --project <repo> \
  --framework <repo_framework> \
  --update --merge --yes
```

`--yes` suppresses the per-file interactive confirmation prompt for batch runs. Only use it when you have reviewed the dry-run output for all repos in the batch.

### Post-Fleet

1. Update `last_audit_date` for each successfully updated repo in `references/adjacent-repos.md`
2. Append changelog rows to `references/adjacent-repos-changelog.csv` (timestamp, repo, action, files changed, summary)
3. Run `@conflict-auditor` to verify the updated registry is internally consistent

---

## Registry Maintenance (Protocol 4)

After each fleet update, the registry requires a maintenance pass:

1. Scan `<filesystem root>` for new repositories with agent infrastructure directories
2. Add new entries with appropriate `update_scope` and `approval_gate`
3. Remove or archive entries for repos that no longer exist
4. Update `last_audit_date` and summary statistics
5. Commit the updated registry with a meaningful message

New repos discovered without a `_build-description.json` are recorded as `excluded` in the registry and excluded from subsequent fleet updates.

---

## Governance: Constitutional Rule #11

Any action that modifies files in a repository other than the project's `PRIMARY_OUTPUT_DIR` must first be assessed by `@repo-liaison` and cleared by `@security`. This applies without exception:

- Template propagation to adjacent repos
- Bridge artifact writes into another repo's `references/` directory
- Interop bundle emission into another repo's path

The `@repo-liaison` assessment (Protocol 1) and `@security` clearance are sequential prerequisites, not parallel. The security clearance cannot be requested until the impact report is complete.

---

## Troubleshooting

### "Adjacent repo path not found on disk"

**Cause:** The registry entry's `repo_path` no longer exists (repo was moved, renamed, or deleted).

**Fix:** Update the registry entry with the new path, or mark it `excluded` if the repo no longer exists.

### Protocol 2 blocked by `@security` — `HALT` decision

**Cause:** The security assessment found a risk that cannot be mitigated by conditions.

**Fix:** Do not proceed. Document the outcome in `references/security-decisions.log.csv` with `decision = HALT`. Surface to the user with the reasoning from the security assessment.

### Adjacent repo has its own orchestrator but is not in the registry

**Fix:** Add the repo to the registry with `orchestrator_present = true` and `update_scope = manual_review`. Route all proposed changes for it through Protocol 3 until you have confirmed the other orchestrator's policy.
