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

# AgentTeamsModule — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in AgentTeamsModule.

---

<!-- AGENTTEAMS:BEGIN project_overview v=1 -->
## Project Overview

**Name:** AgentTeamsModule
**Goal:** A Python module that automatically generates complete, coordinated AI agent teams for any project from a single description file. The module provides a 4-tier agent taxonomy (orchestrator, governance, domain, workstream expert), a template library, a rendering pipeline, and framework adapters for VS Code Copilot, Copilot CLI, and Claude.
**Deliverable type:** Python pipeline modules (ingest, analyze, render, emit), Agent template library (.template.md files), JSON schemas for project description and team manifest, Framework adapters (copilot-vscode, copilot-cli, claude), CLI entry point (build_team.py), Example project briefs and Test suite
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
| `docs/` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |
<!-- AGENTTEAMS:END directory_structure -->

---

<!-- AGENTTEAMS:BEGIN output_conventions v=1 -->
## Output Conventions

- All primary deliverables are authored in `src/` as `Python pipeline modules (ingest, analyze, render, emit), Agent template library (.template.md files), JSON schemas for project description and team manifest, Framework adapters (copilot-vscode, copilot-cli, claude), CLI entry point (build_team.py), Example project briefs and Test suite`
- Compiled output lives in `dist/` and is **never edited directly**
- Figures are generated from source files in `docs/figures/` — source files are authoritative
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
- `@reference-manager` — manages the reference/bibliography database
- `@output-compiler` — assembles components into the final deliverable package
- `@retrieval-integrator` — validates retrieval query, maintenance, and trigger contracts

### Workstream Experts
- `@template-library-expert` — Template Library
- `@pipeline-core-expert` — Pipeline Core (ingest → analyze → render → emit)
- `@framework-adapters-expert` — Framework Adapters
- `@schemas-expert` — JSON Schemas
- `@cli-and-examples-expert` — CLI Entry Point and Examples
- `@test-suite-expert` — Test Suite
<!-- AGENTTEAMS:END agent_team -->

---

<!-- AGENTTEAMS:BEGIN tone_and_style v=1 -->
## Tone and Style

Default to terse output for read-only auditor and governance roles
(`@security`, `@adversarial`, `@code-hygiene`, `@conflict-auditor`,
`@navigator`, `@quality-auditor`, `@technical-validator`,
`@post-production-auditor`, `@module-doc-validator`,
`@reference-manager` in read mode): respond in ≤200 words unless
the task requires longer output. Producing roles
(`@primary-producer`, `@module-doc-author`, `@content-enricher`,
`@output-compiler`, `@orchestrator` when summarizing a multi-step
session) emit the deliverable in full and are exempt from this
default.

Terse mode reduces consumer-harness token consumption on the
common case of audit-and-route turns. Producing roles override the
default explicitly by saying so in their first line.
<!-- AGENTTEAMS:END tone_and_style -->

<!-- AGENTTEAMS:BEGIN authority_hierarchy v=1 -->
## Authority Hierarchy

1. **Template library** (`templates/`) — agent file structure, placeholder conventions, agent taxonomy patterns
2. **JSON schemas** (`schemas/`) — input/output contract accuracy (project-description.schema.json, team-manifest.schema.json)
3. **Python source pipeline** (`src/`) — pipeline logic, placeholder resolution, framework adapter behavior
4. **PLACEHOLDER-CONVENTIONS.md** (`templates/PLACEHOLDER-CONVENTIONS.md`) — placeholder syntax rules (auto-resolved and manual-required token formats)
5. **Implementation plan** (`build-team-plan.md`) — architectural decisions, agent taxonomy, module design rationale
<!-- AGENTTEAMS:END authority_hierarchy -->

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `docs/`
6. **Voice fidelity** — style governance rulings are authoritative when a style-governance agent is present
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session
9. **Every request must generate a plan** — any request involving two or more implementation steps (steps that write, create, rename, delete, or make agent decisions) must produce: (a) a summary saved to `tmp/by-week/YYYY-Www/<plan-slug>.plan.md` and (b) a step-by-step CSV saved to `tmp/by-week/YYYY-Www/<plan-slug>.steps.csv` before the first step executes; the CSV must include columns: `step`, `agent`, `action`, `inputs`, `outputs`, `status`, `notes` (and may include an optional `depends_on` column listing the `step` ids a row depends on, enabling parallelization analysis); initial `status` for all rows is `pending`; after each step completes, pass remaining steps through `@adversarial` and `@conflict-auditor` before proceeding; create the week folder if it does not exist and read legacy undated plans from `tmp/` when canonical week-organized storage is absent
10. **Completed plans must be captured in daily work summaries** — when a plan reaches all `done` during a session, invoke `@work-summarizer` to append/update `workSummaries/daily/YYYY-MM-DD.md` before closeout
11. **Post-Deliverable Retrospective** — When a primary deliverable is produced or revised and has passed its audit chain, evaluate the session for (a) generalizable lessons about this project's own agent infrastructure and (b) remediation items for the AgentTeamsModule tool itself; audit both via `@adversarial` and `@conflict-auditor`; apply (a) via `@agent-updater` and log (b) to `references/agentteams-remediation-log.csv`. Also fires at the close of any ad-hoc session that produced or revised a deliverable without entering a numbered workflow. Full semantics: `references/retrospective-remediation.reference.md` (in the `.claude/agents/` team) — do not restate them here.

---

<!-- AGENTTEAMS:BEGIN source_repositories v=1 -->
## Source Repositories

- `templates/` — agent file structure, placeholder conventions, agent taxonomy patterns
- `schemas/` — input/output contract accuracy (project-description.schema.json, team-manifest.schema.json)
- `src/` — pipeline logic, placeholder resolution, framework adapter behavior
- `templates/PLACEHOLDER-CONVENTIONS.md` — placeholder syntax rules (auto-resolved and manual-required token formats)
- `build-team-plan.md` — architectural decisions, agent taxonomy, module design rationale
<!-- AGENTTEAMS:END source_repositories -->

---

## Style Rules

- stdlib-only: no external dependencies in src/ (pytest is dev-only)
- All public functions must have docstrings with Args/Returns/Raises
- Type annotations required on all public function signatures
- Templates use `{UPPER_SNAKE_CASE}` for auto-resolved placeholders and `{MANUAL:UPPER_SNAKE_CASE}` for human-required
- Agent templates must include YAML front matter with required keys: name, description, user-invokable, tools, model
- Every agent template must contain an Invariant Core section marked with the stop-sign emoji
