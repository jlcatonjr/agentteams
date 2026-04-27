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

# WebAppBackend — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in WebAppBackend.

---

<!-- AGENTTEAMS:BEGIN project_overview v=1 -->
## Project Overview

**Name:** WebAppBackend
**Goal:** Build a Python FastAPI backend for a task management web application, including REST API endpoints, database models, authentication, and automated tests.
**Deliverable type:** Python modules, OpenAPI documentation and test suite
**Output format:** Python 3.11 modules
<!-- AGENTTEAMS:END project_overview -->

---

<!-- AGENTTEAMS:BEGIN directory_structure v=1 -->
## Directory Structure

| Path | Purpose |
|------|---------|
| `src/` | Primary authored deliverables |
| `dist/` | Compiled/converted output artifacts |
| `docs/figures/` | Diagrams and figures |
| `{MANUAL:REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |
<!-- AGENTTEAMS:END directory_structure -->

---

<!-- AGENTTEAMS:BEGIN output_conventions v=1 -->
## Output Conventions

- All primary deliverables are authored in `src/` as `Python modules, OpenAPI documentation and test suite`
- Compiled output lives in `dist/` and is **never edited directly**
- Figures are generated from source files in `docs/figures/` — source files are authoritative
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
- `@repo-liaison` — cross-repository impact tracking and coordination
- `@git-operations` — git/github operations and merge strategy workflow

### Domain Agents
- `@primary-producer` — drafts and revises primary deliverables
- `@quality-auditor` — read-only structural and prose quality audit
- `@cohesion-repairer` — repairs within-section cohesion failures
- `@technical-validator` — verifies technical accuracy against authority sources
- `@format-converter` — converts deliverables to final output format
- `@output-compiler` — assembles components into the final deliverable package
- `@tool-doc-researcher` — specialized domain agent

### Workstream Experts
- `@auth-module-expert` — Authentication Module
- `@tasks-api-expert` — Tasks API
<!-- AGENTTEAMS:END agent_team -->

---

<!-- AGENTTEAMS:BEGIN authority_hierarchy v=1 -->
## Authority Hierarchy

1. **OpenAPI specification** (`docs/openapi.yaml`) — API contract accuracy
2. **Database schema** (`src/models/schema.sql`) — data model accuracy
<!-- AGENTTEAMS:END authority_hierarchy -->

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `{MANUAL:REFERENCE_DB_PATH}`
6. **Voice fidelity** — style governance rulings are authoritative when a style-governance agent is present
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session
9. **Every request must generate a plan** — any request involving two or more implementation steps (steps that write, create, rename, delete, or make agent decisions) must produce: (a) a summary saved to `tmp/<plan-slug>.plan.md` and (b) a step-by-step CSV saved to `tmp/<plan-slug>.steps.csv` before the first step executes; the CSV must include columns: `step`, `agent`, `action`, `inputs`, `outputs`, `status`, `notes`; initial `status` for all rows is `pending`; after each step completes, pass remaining steps through `@adversarial` and `@conflict-auditor` before proceeding; create `tmp/` if it does not exist

---

<!-- AGENTTEAMS:BEGIN source_repositories v=1 -->
## Source Repositories

- `docs/openapi.yaml` — API contract accuracy
- `src/models/schema.sql` — data model accuracy
<!-- AGENTTEAMS:END source_repositories -->

---

## Style Rules

- All public functions must have docstrings
- Type annotations required for all function signatures
- No mutable default arguments
