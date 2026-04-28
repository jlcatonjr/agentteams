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

# SalesDataPipeline — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in SalesDataPipeline.

---

<!-- AGENTTEAMS:BEGIN project_overview v=1 -->
## Project Overview

**Name:** SalesDataPipeline
**Goal:** Build an ETL pipeline that ingests daily sales CSV exports, validates and transforms them, loads them into a PostgreSQL warehouse, and produces weekly summary reports.
**Deliverable type:** Python ETL modules, SQL transformation scripts and weekly PDF reports
**Output format:** Python 3.11 modules and PDF reports
<!-- AGENTTEAMS:END project_overview -->

---

<!-- AGENTTEAMS:BEGIN directory_structure v=1 -->
## Directory Structure

| Path | Purpose |
|------|---------|
| `src/` | Primary authored deliverables |
| `reports/` | Compiled/converted output artifacts |
| `reports/figures/` | Diagrams and figures |
| `workSummaries/` | Daily, weekly, and monthly work summaries |
| `{MANUAL:REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |
<!-- AGENTTEAMS:END directory_structure -->

---

<!-- AGENTTEAMS:BEGIN output_conventions v=1 -->
## Output Conventions

- All primary deliverables are authored in `src/` as `Python ETL modules, SQL transformation scripts and weekly PDF reports`
- Compiled output lives in `reports/` and is **never edited directly**
- Figures are generated from source files in `reports/figures/` — source files are authoritative
- Every deliverable must correspond to a Component Spec defined by a workstream expert
- Work summaries are authored in `workSummaries/` from canonical `tmp/by-week/` plan artifacts, legacy `tmp/` fallbacks, and git history
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
- `@work-summarizer` — synthesizes daily/weekly/monthly work summaries from plan artifacts and git history
- `@primary-producer` — drafts and revises primary deliverables
- `@quality-auditor` — read-only structural and prose quality audit
- `@cohesion-repairer` — repairs within-section cohesion failures
- `@technical-validator` — verifies technical accuracy against authority sources
- `@format-converter` — converts deliverables to final output format
- `@output-compiler` — assembles components into the final deliverable package
- `@visual-designer` — creates and revises diagrams and figures
- `@module-doc-author` — specialized domain agent
- `@module-doc-validator` — specialized domain agent
- `@tool-doc-researcher` — specialized domain agent

### Workstream Experts
- `@ingest-expert` — Ingest Module
- `@transform-expert` — Transform Module
- `@load-expert` — Load Module
- `@weekly-report-expert` — Weekly Summary Report
<!-- AGENTTEAMS:END agent_team -->

---

<!-- AGENTTEAMS:BEGIN authority_hierarchy v=1 -->
## Authority Hierarchy

1. **Source CSV schema** (`docs/source-schema.md`) — field names and types in raw data
2. **Warehouse schema** (`sql/warehouse-schema.sql`) — target table structure
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
9. **Every request must generate a plan** — any request involving two or more implementation steps (steps that write, create, rename, delete, or make agent decisions) must produce: (a) a summary saved to `tmp/by-week/YYYY-Www/<plan-slug>.plan.md` and (b) a step-by-step CSV saved to `tmp/by-week/YYYY-Www/<plan-slug>.steps.csv` before the first step executes; the CSV must include columns: `step`, `agent`, `action`, `inputs`, `outputs`, `status`, `notes`; initial `status` for all rows is `pending`; after each step completes, pass remaining steps through `@adversarial` and `@conflict-auditor` before proceeding; create the week folder if it does not exist and read legacy undated plans from `tmp/` when canonical week-organized storage is absent
10. **Completed plans must be captured in daily work summaries** — when a plan reaches all `done` during a session, invoke `@work-summarizer` to append/update `workSummaries/daily/YYYY-MM-DD.md` before closeout

---

<!-- AGENTTEAMS:BEGIN source_repositories v=1 -->
## Source Repositories

- `docs/source-schema.md` — field names and types in raw data
- `sql/warehouse-schema.sql` — target table structure
<!-- AGENTTEAMS:END source_repositories -->

---

## Style Rules

- All SQL queries parameterized — no string concatenation
- Log all row counts at each pipeline stage
- Idempotent loads: upsert on primary key
