<!--
SECTION MANIFEST — copilot-instructions.template.md
| section_id            | designation   | notes                                    |
|-----------------------|---------------|------------------------------------------|
| project_overview      | FENCED        | Name, goal, deliverable type, output fmt |
| directory_structure   | FENCED        | Path/purpose table                       |
| output_conventions    | FENCED        | Authoring and build conventions          |
| agent_team            | FENCED        | Full agent team list                     |
| authority_hierarchy   | FENCED        | Source hierarchy list                    |
| source_repositories   | FENCED        | Authority source entries                 |
| constitutional_rules  | USER-EDITABLE | Project may extend or customise          |
| style_rules           | USER-EDITABLE | Project may extend or customise          |
-->

# {PROJECT_NAME} — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in {PROJECT_NAME}.

---

<!-- AGENTTEAMS:BEGIN project_overview v=1 -->
## Project Overview

**Name:** {PROJECT_NAME}
**Goal:** {PROJECT_GOAL}
**Deliverable type:** {DELIVERABLE_TYPE}
**Output format:** {OUTPUT_FORMAT}
<!-- AGENTTEAMS:END project_overview -->

---

<!-- AGENTTEAMS:BEGIN directory_structure v=1 -->
## Directory Structure

| Path | Purpose |
|------|---------|
| `{PRIMARY_OUTPUT_DIR}` | Primary authored deliverables |
| `{BUILD_OUTPUT_DIR}` | Compiled/converted output artifacts |
| `{FIGURES_DIR}` | Diagrams and figures |
| `{REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |
<!-- AGENTTEAMS:END directory_structure -->

---

<!-- AGENTTEAMS:BEGIN output_conventions v=1 -->
## Output Conventions

- All primary deliverables are authored in `{PRIMARY_OUTPUT_DIR}` as `{DELIVERABLE_TYPE}`
- Compiled output lives in `{BUILD_OUTPUT_DIR}` and is **never edited directly**
- Figures are generated from source files in `{FIGURES_DIR}` — source files are authoritative
- Every deliverable must correspond to a Component Spec defined by a workstream expert
<!-- AGENTTEAMS:END output_conventions -->

---

<!-- AGENTTEAMS:BEGIN agent_team v=1 -->
## Agent Team

### Orchestrator
- `@orchestrator` — coordinates all agents; entry point for all user requests

### Governance Agents
- `@navigator` — project structure and file location
- `@security` — destructive operation clearance
- `@code-hygiene` — architecture enforcement and anti-sprawl auditor
- `@adversarial` — presupposition critic
- `@conflict-auditor` — consistency enforcement
- `@conflict-resolution` — ACCEPT/REJECT/REVISE decisions on flagged conflicts
- `@cleanup` — artifact removal
- `@agent-updater` — documentation synchronization
- `@agent-refactor` — spec compliance and reference extraction

### Domain Agents
{DOMAIN_AGENT_LIST}

### Workstream Experts
{WORKSTREAM_EXPERT_LIST}
<!-- AGENTTEAMS:END agent_team -->

---

<!-- AGENTTEAMS:BEGIN authority_hierarchy v=1 -->
## Authority Hierarchy

{AUTHORITY_HIERARCHY}
<!-- AGENTTEAMS:END authority_hierarchy -->

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `{REFERENCE_DB_PATH}`
6. **Voice fidelity** — style governance rulings are authoritative when a style-governance agent is present
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session

---

<!-- AGENTTEAMS:BEGIN source_repositories v=1 -->
## Source Repositories

{AUTHORITY_SOURCES_LIST}
<!-- AGENTTEAMS:END source_repositories -->

---

## Style Rules

{STYLE_RULES_SUMMARY}
