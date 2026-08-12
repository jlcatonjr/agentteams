# Adjacent Repositories Registry

**Purpose:** Authoritative inventory of all repositories affected by AgentTeamsModule cross-repository operations. Required by Orchestrator Authority Hierarchy and Workflow 9 (Cross-Repository Coordination).

**Last Updated:** 2026-08-10 by Orchestrator (durable-canonical-agent-format plan J.1 registration of 7 adjacent repos; prior fleet audit 2026-05-08)

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
| ~/githubrepositories/researchteam | .github/agents | secondary | yes | full_update | repo-liaison | 2026-07-23 | Real, actively-updated consumer. Also has native `.goose/recipes/` and bridged `.claude/agents/`. Uses its own `researchteam` CLI wrapper (`.researchteam` marker) which auto-integrates on commit (pre-commit hook, layer1-only) and via `researchteam update` (layer1 + layer-2 project-specific file sync from `researchteam@main`). |
| ~/githubrepositories/visualknowledge/collector-management | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-07 | Real, actively-updated consumer with a bridged `.claude/agents/`. **Was unregistered until 2026-08-07** despite appearing in `git-procedures.md`, `bridge-refresh-safety.md` and `security-decisions.log.csv` — Gate 1 of the fleet-update authorization policy HALTs on missing registry entries, so the gap silently blocked cleared work. Subject of the 2026-05-27 `--bridge-refresh` incident that destroyed user content in `.claude/skills/recall.md`: **merge-only, never refresh**. |
| ~/githubrepositories/researchRepositories/OrthodoxLLM | .github/agents | secondary | yes | full_update | repo-liaison | 2026-07-23 | Real, actively-updated consumer; sibling of researchteam (same descriptor lineage — forked/customized brief.json, not a technical dependency). Also has native `.goose/recipes/` and bridged `.claude/agents/`. Uses the same `researchteam` CLI wrapper (`.researchteam` marker → `researchteam@main`). |
| ~/githubrepositories/visualknowledge/vk-services-local | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real consumer with bridged `.claude/agents/`; registered 2026-08-10 (durable-canonical-agent-format plan J.1). Sibling work-copies `vk-services-local-gn0` / `vk-services-local-fix` remain unregistered derivatives. |
| ~/githubrepositories/visualknowledge/vk-api-utils | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real consumer with bridged `.claude/agents/`; registered 2026-08-10 (plan J.1). Sibling work-copy `vk-api-utils-margin` remains an unregistered derivative. |
| ~/githubrepositories/visualknowledge/vk-support | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real consumer with bridged `.claude/agents/`; registered 2026-08-10 (plan J.1). |
| ~/githubrepositories/visualknowledge/colorado-collectors | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real consumer with bridged `.claude/agents/`; registered 2026-08-10 (plan J.1). |
| ~/githubrepositories/visualknowledge/tucson_data_collection | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real data-collection consumer with bridged `.claude/agents/`; registered 2026-08-10 (plan J.1). |
| ~/githubrepositories/GeneralResearchTeam | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Real research consumer; also has native `.goose/recipes/`; registered 2026-08-10 (plan J.1). |
| ~/githubrepositories/CoPilotAgentDocumentation | .github/agents | secondary | yes | full_update | repo-liaison | 2026-08-10 | Documentation consumer with bridged `.claude/agents/`; registered 2026-08-10 (plan J.1). |

### Vendor & Archived

| repo_path | agent_infra_path | relationship | orchestrator | update_scope | approval_gate | last_audit | notes |
|---|---|---|---|---|---|---|---|
| ~/githubrepositories/vendor-code | .github/agents | vendor | no | security_only | orchestrator | 2026-05-08 | Third-party code; minimal agent infrastructure |
| ~/githubrepositories/archived-project | .github/agents | archive | no | manual_review | orchestrator | 2026-05-08 | Archived; updates require explicit approval |

---

## Summary Statistics

- **Total repos registered (enumerated below):** 20
- **Orchestrator present:** 8 (agentteams + 7 consumer repos registered 2026-08-10)
- **Update scope: full_update:** 18
- **Update scope: security_only:** 1 (vendor)
- **Update scope: manual_review:** 1 (archived)
- **Last fleet audit:** 2026-05-08 (fleet-update-all-repositories)

> The 20 rows above are the **named, individually-tracked** repositories. The broader 2026-W19 fleet discovery enumerated ~38 in-scope repos under `~/githubrepositories/` (see `references/fleet-update-scope-boundary.md` §I); only the named representatives plus the vendor/archived exceptions are tracked by row here. The remaining in-scope repos are governed by the `full_update` default and are not individually listed.

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
| 2026-08-07 | Repo Liaison | Added `visualknowledge/collector-management` as a named row | Named throughout the safety docs and the 2026-05-27 incident record, but never registered. Found while gating the skill-layout cross-repo fix — Gate 1 would have HALTed on it. |
| 2026-07-23 | Repo Liaison | Added `researchteam` and `researchRepositories/OrthodoxLLM` as named rows | Both were real, actively-updated consumers of today's agentteams changes but were unregistered — discovered while integrating Rule S-9/CLI-competency updates into both repos |

## 2026-07-24 — `agentteams.research` behavior change (consumers should be aware)

`agentteams/research/search.py` changed in two ways that affect every consumer of the module,
not just Goose teams (it is imported by `reputable.py`, `news.py`, and any downstream project):

- **`web_search` may now issue a second request.** When the search endpoint answers with an
  HTTP-202 challenge, the query is broadened and retried once. Worst-case wall-clock per call
  roughly doubles; callers that fan out searches in a pool (e.g. `reputable.py`) should expect
  the change in timing. The list-returning contract is unchanged.
- **`fetch_text`'s `max_bytes` default rose 40,000 → 400,000** (a 10× larger download per fetch).
  The previous default silently truncated any large page to its navigation header. `max_chars`
  (what actually reaches a caller's context) is unchanged at 4,000.

New `web_search_verbose` returns `(results, note)` for callers that must distinguish "blocked"
from "nothing matched" — the plain `[]` cannot.

