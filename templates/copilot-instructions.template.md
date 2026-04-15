# {PROJECT_NAME} — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in {PROJECT_NAME}.

---

## Project Overview

**Name:** {PROJECT_NAME}
**Goal:** {PROJECT_GOAL}
**Deliverable type:** {DELIVERABLE_TYPE}
**Output format:** {OUTPUT_FORMAT}

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `{PRIMARY_OUTPUT_DIR}` | Primary authored deliverables |
| `{BUILD_OUTPUT_DIR}` | Compiled/converted output artifacts |
| `{FIGURES_DIR}` | Diagrams and figures |
| `{REFERENCE_DB_PATH}` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |

---

## Output Conventions

- All primary deliverables are authored in `{PRIMARY_OUTPUT_DIR}` as `{DELIVERABLE_TYPE}`
- Compiled output lives in `{BUILD_OUTPUT_DIR}` and is **never edited directly**
- Figures are generated from source files in `{FIGURES_DIR}` — source files are authoritative
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
{DOMAIN_AGENT_LIST}

### Workstream Experts
{WORKSTREAM_EXPERT_LIST}

---

## Authority Hierarchy

{AUTHORITY_HIERARCHY}

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `{REFERENCE_DB_PATH}`
6. **Voice fidelity** — `@style-guardian` is the sole arbiter of voice deviation rulings
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session

---

## Source Repositories

{AUTHORITY_SOURCES_LIST}

---

## Style Rules

{STYLE_RULES_SUMMARY}
