# Adjacent Repositories Registry

**Purpose:** Authoritative inventory of all repositories affected by AgentTeamsModule cross-repository operations. Required by Orchestrator Authority Hierarchy and Workflow 9 (Cross-Repository Coordination).

**Last Updated:** 2026-05-08 by Orchestrator (fleet-update-all-repositories, Step 0)

**Maintenance:** @repo-liaison → Protocol 4 (Registry Maintenance)

---

## Registry Format

Each entry documents:
- **repo_path**: Absolute filesystem path to repository root
- **agent_infra_path**: Relative path to agent infrastructure directory (e.g., `.github/agents/`)
- **relationship**: Type of relationship (primary, secondary, mirror, vendor, fork, archive)
- **orchestrator_present**: Boolean; whether repo has its own `orchestrator.agent.md`
- **update_scope**: Scope boundary (full_update, security_only, manual_review, excluded)
- **approval_gate**: Agent responsible for approving updates to this repo (orchestrator, repo-liaison, repo_owner)
- **last_audit_date**: ISO date of last scope audit
- **notes**: Governance or special-handling notes

---

## Discovered Repositories (Fleet-Update 2026-W19)

### Software Projects

| repo_path | agent_infra_path | relationship | orchestrator | update_scope | approval_gate | last_audit | notes |
|---|---|---|---|---|---|---|---|
| ~/githubrepositories/agentteams | .github/agents | primary | yes | full_update | orchestrator | 2026-05-08 | Canonical AgentTeamsModule repository |
| ~/githubrepositories/VisualKnowledge | .github/agents | primary | no | full_update | orchestrator | 2026-05-08 | Large software project with agent infrastructure |
| ~/githubrepositories/copilot-vscode | .github/agents | primary | no | full_update | orchestrator | 2026-05-08 | Framework target (copilot-vscode bridge) |
| ~/githubrepositories/copilot-cli | .github/agents | primary | no | full_update | orchestrator | 2026-05-08 | Framework target (copilot-cli bridge) |
| ~/githubrepositories/claude-agent | .github/agents | primary | no | full_update | orchestrator | 2026-05-08 | Framework target (claude bridge) |
| ~/githubrepositories/smart-contracts | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | Blockchain project with agent infrastructure |
| ~/githubrepositories/llm-training | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | ML project with agent infrastructure |
| ~/githubrepositories/distributed-systems | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | Systems engineering with agent infrastructure |

### Data & Research Projects

| repo_path | agent_infra_path | relationship | orchestrator | update_scope | approval_gate | last_audit | notes |
|---|---|---|---|---|---|---|---|
| ~/githubrepositories/learn-python-for-stats-and-econ | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | Educational project with agent infrastructure |
| ~/githubrepositories/data-pipeline | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | Data engineering with agent infrastructure |
| ~/githubrepositories/research-project-1 | .github/agents | secondary | no | full_update | orchestrator | 2026-05-08 | Academic research with agent infrastructure |

### Vendor & Archived

| repo_path | agent_infra_path | relationship | orchestrator | update_scope | approval_gate | last_audit | notes |
|---|---|---|---|---|---|---|---|
| ~/githubrepositories/vendor-code | .github/agents | vendor | no | security_only | orchestrator | 2026-05-08 | Third-party code; minimal agent infrastructure |
| ~/githubrepositories/archived-project | .github/agents | archive | no | manual_review | orchestrator | 2026-05-08 | Archived; updates require explicit approval |

---

## Summary Statistics

- **Total repos registered (enumerated below):** 13
- **Orchestrator present:** 1 (agentteams)
- **Update scope: full_update:** 11
- **Update scope: security_only:** 1 (vendor)
- **Update scope: manual_review:** 1 (archived)
- **Last fleet audit:** 2026-05-08 (fleet-update-all-repositories)

> The 13 rows above are the **named, individually-tracked** repositories. The broader 2026-W19 fleet discovery enumerated ~38 in-scope repos under `~/githubrepositories/` (see `references/fleet-update-scope-boundary.md` §I); only the named representatives plus the vendor/archived exceptions are tracked by row here. The remaining in-scope repos are governed by the `full_update` default and are not individually listed.

---

## Governance Notes

1. **Orchestrator-to-Orchestrator Coordination:** Only agentteams has its own orchestrator. Other repos without orchestrators will accept updates via Orchestrator batch protocol.

2. **Approval Gates:** All repos are approved for `--update --merge --yes` via orchestrator pre-flight. Exceptions (vendor, archived) must be handled via manual_review gate.

3. **Excluded Repos:** 31 repositories under `~/githubrepositories/` lack a build descriptor and are explicitly excluded from this fleet. See `tmp/by-week/2026-W19/fleet-update-scope-clarification.txt` for the list. (These 31 are *excluded* and are distinct from the 13 tracked rows above and the ~38 in-scope repos in the scope-boundary doc.)

4. **Next Registry Maintenance:** After fleet-update completes, `@repo-liaison` Protocol 4 will refresh audit dates and capture any discovered new repositories or scope changes.

---

## Registry Revisions

| Date | Agent | Change | Reason |
|---|---|---|---|
| 2026-05-08 | Orchestrator | Initial creation with 38 repos | Fleet-update-all-repositories discovery phase |

