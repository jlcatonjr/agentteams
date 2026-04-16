# ProjectRepositories — Copilot Instructions

> This file defines the conventions, authority hierarchy, and agent team structure for all GitHub Copilot agents in ProjectRepositories.

---

## Project Overview

**Name:** ProjectRepositories
**Goal:** A collection of empirical economics and data science research projects analyzing monetary policy, fiscal dynamics, agent-based economic models, and energy policy using Python, Jupyter notebooks, statistical modeling, and interactive visualization. Each sub-project produces research-grade analysis paired with interactive HTML charts or data-driven whitepapers.
**Deliverable type:** Jupyter notebooks, interactive HTML visualizations, Python analysis modules and research whitepapers
**Output format:** Jupyter notebooks and HTML reports

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `*/outputs/` | Primary authored deliverables |
| `*/Whitepaper/` | Compiled/converted output artifacts |
| `*/outputs/` | Diagrams and figures |
| `.github/agents/references/project-references.bib` | Reference/bibliography database |
| `.github/agents/` | Agent definition files |
| `.github/agents/references/` | Shared reference data |

---

## Output Conventions

- All primary deliverables are authored in `*/outputs/` as `Jupyter notebooks, interactive HTML visualizations, Python analysis modules and research whitepapers`
- Compiled output lives in `*/Whitepaper/` and is **never edited directly**
- Figures are generated from source files in `*/outputs/` — source files are authoritative
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

### Workstream Experts
- `@crisis-credit-allocation-expert` — Crisis and Credit Allocation
- `@fed-response-dag-expert` — Federal Reserve Response Function DAG Analysis
- `@prairie-prosperity-expert` — More Prairie Prosperity — Fiscal and Economic Policy in North Dakota
- `@sugarscape-expert` — Sugarscape Agent-Based Model
- `@visualize-energy-data-expert` — Visualize Energy Data

---

## Authority Hierarchy

1. **FRED / Federal Reserve Economic Data** (`MappingTheFederalReserve'sResponseFunctionWithDirectedAcyclicGraphs/datlib/FRED.py`) — Federal Reserve monetary data fetch conventions and series codes
2. **datlib library** (`MappingTheFederalReserve'sResponseFunctionWithDirectedAcyclicGraphs/datlib/`) — DAG construction, time-series testing, statistical analysis, and plotting conventions for the Fed DAG project
3. **homebrewedFunctions library** (`MorePrairieProsperity/homebrewedFunctions/`) — Shared helper functions for the Prairie Prosperity fiscal analysis
4. **Crisis and Credit Allocation notebook** (`Crisis and Credit Allocation/Crisis and Credit Allocation Data.ipynb`) — Canonical analysis script for crisis-era banking and credit data
5. **Sugarscape model source** (`Sugarscape/`) — Agent-based model implementation — Agent.py, Model.py, Patch.py are the authoritative model definitions

---

## Constitutional Rules

1. **Security first** — destructive operations require `@security` clearance
2. **Code hygiene second** — code changes require `@code-hygiene` audit before merge
3. **Authority hierarchy is ground truth** — no agent may contradict a higher-authority source
4. **Primary deliverables are the canonical output** — build artifacts are derived, never primary
5. **No fabricated references** — every citation must be verifiable in `.github/agents/references/project-references.bib`
6. **Voice fidelity** — style governance rulings are authoritative when a style-governance agent is present
7. **Living documentation** — agent docs must not accumulate stale content
8. **Always close with `@conflict-auditor`** — required after any multi-file change session

---

## Source Repositories

- `MappingTheFederalReserve'sResponseFunctionWithDirectedAcyclicGraphs/datlib/FRED.py` — Federal Reserve monetary data fetch conventions and series codes
- `MappingTheFederalReserve'sResponseFunctionWithDirectedAcyclicGraphs/datlib/` — DAG construction, time-series testing, statistical analysis, and plotting conventions for the Fed DAG project
- `MorePrairieProsperity/homebrewedFunctions/` — Shared helper functions for the Prairie Prosperity fiscal analysis
- `Crisis and Credit Allocation/Crisis and Credit Allocation Data.ipynb` — Canonical analysis script for crisis-era banking and credit data
- `Sugarscape/` — Agent-based model implementation — Agent.py, Model.py, Patch.py are the authoritative model definitions

---

## Style Rules

- Jupyter notebooks are the primary authoring surface; Python .py files are supporting libraries or model classes
- Interactive HTML outputs use Plotly; static plots use matplotlib
- Data files (CSV, Excel, parquet) are raw inputs — never modify source data in notebooks, only transform into derived frames
- Each sub-project is self-contained in its own directory; cross-project imports are not permitted
- Cite academic sources inline in notebook markdown cells using AuthorYear convention
