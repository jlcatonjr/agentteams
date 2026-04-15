# WebAppBackend — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in WebAppBackend.

---

## Project Overview

**Name:** WebAppBackend
**Goal:** Build a Python FastAPI backend for a task management web application, including REST API endpoints, database models, authentication, and automated tests.
**Deliverable type:** Python modules, OpenAPI documentation and test suite
**Output format:** Python 3.11 modules

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `src/` | Primary authored deliverables |
| `dist/` | Compiled/converted output artifacts |
| `docs/figures/` | Diagrams and figures |
| `{MANUAL:REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |

---

## Output Conventions

- All primary deliverables are authored in `src/` as `Python modules, OpenAPI documentation and test suite`
- Compiled output lives in `dist/` and is **never edited directly**
- Figures are generated from source files in `docs/figures/` — source files are authoritative
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
- `@tool-doc-researcher` — specialized domain agent

### Workstream Experts
- `@auth-module-expert` — Authentication Module
- `@tasks-api-expert` — Tasks API

---

## Authority Hierarchy

1. **OpenAPI specification** (`docs/openapi.yaml`) — API contract accuracy
2. **Database schema** (`src/models/schema.sql`) — data model accuracy

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

- `docs/openapi.yaml` — API contract accuracy
- `src/models/schema.sql` — data model accuracy

---

## Style Rules

- All public functions must have docstrings
- Type annotations required for all function signatures
- No mutable default arguments
