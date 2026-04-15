# ResearchPaperProject — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in ResearchPaperProject.

---

## Project Overview

**Name:** ResearchPaperProject
**Goal:** Produce a peer-reviewed academic paper on multi-agent coordination theory, progressing from an outline through final LaTeX manuscript.
**Deliverable type:** HTML chapter drafts, LaTeX manuscript and BibTeX bibliography
**Output format:** PDF via LaTeX

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `html/chapters/` | Primary authored deliverables |
| `manuscript/` | Compiled/converted output artifacts |
| `figures/` | Diagrams and figures |
| `references/bibliography.bib` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |

---

## Output Conventions

- All primary deliverables are authored in `html/chapters/` as `HTML chapter drafts, LaTeX manuscript and BibTeX bibliography`
- Compiled output lives in `manuscript/` and is **never edited directly**
- Figures are generated from source files in `figures/` — source files are authoritative
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
- `@style-guardian` — enforces voice and style fidelity
- `@technical-validator` — verifies technical accuracy against authority sources
- `@format-converter` — converts deliverables to final output format
- `@reference-manager` — manages the reference/bibliography database
- `@output-compiler` — assembles components into the final deliverable package
- `@visual-designer` — creates and revises diagrams and figures
- `@module-doc-author` — specialized domain agent
- `@module-doc-validator` — specialized domain agent
- `@tool-doc-researcher` — specialized domain agent

### Workstream Experts
- `@ch01-introduction-expert` — Chapter 1: Introduction
- `@ch02-literature-expert` — Chapter 2: Literature Review

---

## Authority Hierarchy

1. **Published papers by James Caton** (`sources/papers/`) — theoretical claims
2. **Agent source files** (`.github/agents/`) — agent documentation accuracy

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `references/bibliography.bib`
6. **Voice fidelity** — `@style-guardian` is the sole arbiter of voice deviation rulings
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session

---

## Source Repositories

- `sources/papers/` — theoretical claims
- `.github/agents/` — agent documentation accuracy

---

## Style Rules

- Avoid passive voice except in methodology sections
- Use em-dashes without spaces
- Cite page numbers for direct quotations
