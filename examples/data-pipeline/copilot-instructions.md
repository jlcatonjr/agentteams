# SalesDataPipeline — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in SalesDataPipeline.

---

## Project Overview

**Name:** SalesDataPipeline
**Goal:** Build an ETL pipeline that ingests daily sales CSV exports, validates and transforms them, loads them into a PostgreSQL warehouse, and produces weekly summary reports.
**Deliverable type:** Python ETL modules, SQL transformation scripts and weekly PDF reports
**Output format:** Python 3.11 modules and PDF reports

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `src/` | Primary authored deliverables |
| `reports/` | Compiled/converted output artifacts |
| `reports/figures/` | Diagrams and figures |
| `{MANUAL:REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |

---

## Output Conventions

- All primary deliverables are authored in `src/` as `Python ETL modules, SQL transformation scripts and weekly PDF reports`
- Compiled output lives in `reports/` and is **never edited directly**
- Figures are generated from source files in `reports/figures/` — source files are authoritative
- Every deliverable must correspond to a Component Spec defined by a workstream expert

---

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

---

## Authority Hierarchy

1. **Source CSV schema** (`docs/source-schema.md`) — field names and types in raw data
2. **Warehouse schema** (`sql/warehouse-schema.sql`) — target table structure

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `{MANUAL:REFERENCE_DB_PATH}`
6. **Voice fidelity** — `@style-guardian` is the sole arbiter of voice deviation rulings
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session

---

## Source Repositories

- `docs/source-schema.md` — field names and types in raw data
- `sql/warehouse-schema.sql` — target table structure

---

## Style Rules

- All SQL queries parameterized — no string concatenation
- Log all row counts at each pipeline stage
- Idempotent loads: upsert on primary key
