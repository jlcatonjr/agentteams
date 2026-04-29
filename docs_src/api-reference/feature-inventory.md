---
title: Feature Inventory
description: Comprehensive enumeration of all AgentTeams capabilities, organized by category
---

# AgentTeams — Feature Inventory

**Version:** 0.1.0 (Released 2026-04-15)

Features below are grouped by capability area. Items described here have been implemented and are
available on the `main` branch. The last formal versioned release is **0.1.0 (2026-04-15)**;
everything listed reflects the current development state.

---

## Summary

--8<-- "docs_src/assets/feature-summary-table.md"

---

## Core Pipeline

### Team Generation

1. **Agent Teams Module** — Generate a complete, coordinated AI agent team from a single project description file
2. **Four-Tier Agent Architecture** — Orchestrator → Governance Agents → Domain Agents → Workstream Experts
3. **Project Description Ingestion** — Load project briefs from `.json` or `.md` format
4. **Project Directory Scanning** — Scan an existing project directory to supplement missing brief fields
5. **Project Type Classification** — Infer the deliverable type from the project goal
6. **Agent Archetype Selection** — Select the appropriate archetype set from the 9-archetype library
7. **Team Manifest Generation** — Build complete team composition metadata
8. **Template Placeholder Resolution** — Resolve `{AUTO:token}` and `{MANUAL:token}` placeholders

### Core Pipeline Modules

9. **`ingest` Module** — Load and normalize project descriptions; parse JSON/Markdown; scan directories
10. **`analyze` Module** — Classify project type; select archetypes; detect tool agents; build manifest
11. **`render` Module** — Resolve placeholders in templates; compute template hashes for drift detection
12. **`emit` Module** — Write rendered files to disk with dry-run and overwrite-protection
13. **`drift` Module** — Detect content drift (template hash comparison) and structural drift (team composition)
14. **`scan` Module** — Proactive security scan for PII paths, credentials, unresolved placeholders
15. **`audit` Module** — Static post-generation audit; optional AI-powered review via Copilot CLI
16. **`remediate` Module** — Auto-correction support via Copilot CLI after audit findings

### Visualization & Graph

17. **`graph` Module** — Directed graph inference for team topology
18. **Mermaid Output** — Team topology diagram in Mermaid format
19. **DOT Output** — GraphViz format for advanced visualization
20. **JSON Output** — Machine-readable team topology
21. **Markdown Output** — Human-readable topology; auto-generated in `references/pipeline-graph.md`

### Framework Adapters

22. **`copilot-vscode` Adapter** — `.agent.md` files with YAML front matter; outputs to `.github/agents/`
23. **`copilot-cli` Adapter** — Plain `.md` system prompts; outputs to `.github/copilot/`
24. **`claude` Adapter** — Claude Code front matter `.md` + `CLAUDE.md`; outputs to `.claude/agents/`
25. **Framework-Agnostic Interface** — Extensible adapter base class for adding new frameworks

### CLI

26. **`agentteams` / `build_team.py` CLI** — Command-line interface wiring all pipeline stages
27. **`--description`** — Path to project brief (`.json` or `.md`)
28. **`--project`** — Target project directory
29. **`--framework`** — Output framework: `copilot-vscode`, `copilot-cli`, or `claude`
30. **`--dry-run`** — Preview output without writing files
31. **`--overwrite`** — Allow overwriting existing agent files
32. **`--self`** — Self-maintenance: regenerate the module's own agent team
33. **`--post-audit`** — Run static + AI-powered conflict and presupposition review

### Templates & Schemas

34. **11 Universal Governance Templates** — One tier-2 template per governance agent archetype
35. **20 Domain Templates** — 14 project-type archetype templates (incl. `content-enricher` and `work-summarizer`) plus 6 tool templates
36. **3 Builder Templates** — Team Builder agent variants
37. **6 Workstream Expert Templates** — Component-scoped expertise patterns
38. **`project-description.schema.json`** — JSON Schema for brief validation
39. **`team-manifest.schema.json`** — JSON Schema for team manifest validation

The `universal/` template library includes the tier-1 Orchestrator template and all 11 always-included tier-2 governance templates.

---

## Enrichment

40. **`enrich` Package** — Context-aware auto-enrichment pipeline
41. **Rule-Based Fill Generation** — Pattern-matched placeholder completion
42. **Jupyter Notebook Scanning** — Extract evidence and examples from `.ipynb` sources
43. **Tool Catalog Integration** — Reference available tools and frameworks for auto-fill
44. **LLM Model Detection** — Identify referenced models and optimize prompts
45. **Enrichment Coverage Audit** — Enumerate unresolved defaults; export `references/defaults-audit.csv`
46. **Dynamic Tool Detection** — Automatic framework/tool identification from project structure
47. **Auto-PyPI Metadata Fetch** — Fetch package metadata for tool catalog enrichment
48. **Agent Integration Audit** — Verify all agents have required integration details
49. **Agent Knowledge Updates** — Incorporate discovered tool knowledge into agent content
50. **`--enrich` Flag** — Run enrichment pass after generation

---

## Section Fencing & Safe Merges

51. **`AGENTTEAMS:BEGIN/END` Fence Markers** — Delineate managed regions in agent files
52. **`FENCE-CONVENTIONS.md`** — Specification for fence marker usage and authoring rules
53. **Non-Destructive Merge Mode (`--merge`)** — Update fenced regions only; preserve all user content outside fences
54. **`--update --merge` Safety Fix** — `--update` now honors `--merge`; no longer silently overwrites user content
55. **11 Instrumented Templates** — All governance and domain templates ship with fence markers

---

## Security Intelligence

56. **`security_refs` Module** — Render live threat intelligence into generated security reference files
57. **Live CVE/CISA-KEV/EPSS Data** — Pull current threat data on every pipeline run
58. **`--security-offline` Flag** — Disable live intelligence fetching for air-gapped or offline runs
59. **`--security-max-items` Flag** — Cap the number of live threat items rendered
60. **`--security-no-nvd` Flag** — Skip NVD fetching while retaining CISA-KEV and EPSS data
61. **Non-Deterministic Snapshot Exclusion** — `test_snapshot_comparison` skips live-data files

---

## Migration & Update

62. **`--migrate` Flag** — One-step fencing migration: tags HEAD as `pre-fencing-snapshot`, then regenerates all files with fenced templates
63. **`--revert-migration` Flag** — Undo a migration by resetting to `pre-fencing-snapshot` git tag
64. **`--update` Mode** — Incremental re-render; preserves manual placeholder values
65. **`--prune` Mode** — Remove stale agent components from an existing team
66. **`--auto-correct` Flag** — Invoke Copilot CLI to repair post-audit findings; reruns audit to confirm
67. **`--scan-security` Flag** — Proactive scan for PII paths, credential patterns, unresolved placeholders
68. **`available_workflows` Fenced Section** — Workflow documentation fenced so `--update --merge` propagates future changes while preserving project-specific rules
69. **Update Deployment Protocol** — Documented multi-step update procedure: dry-run, backup verify, git diff capture, outside-fence analysis (OK/WARN/ERROR), WARN review gate

---

## Safety & Backups

70. **Automatic Backup Before Writes** — Timestamped backup of all agent files before any `--overwrite`, `--merge`, or `--update` run
71. **Backup Storage** — `.agentteams-backups/YYYYMMDD-HHMMSS/` (excluded from git)
72. **`--no-backup` Flag** — Suppress backup creation for CI environments
73. **`--list-backups` Flag** — List available backup timestamps
74. **`--restore-backup [TIMESTAMP|latest]` Flag** — Restore a specific backup
75. **`emit.backup_output_dir()` / `emit.list_backups()` / `emit.restore_backup()`** — Public backup API

---

## Governance Agents

The following eleven agents are included in every generated team, regardless of project type.

76. **`@navigator`** — Project structure mapping, file location queries, dependency tracking (read-only)
77. **`@security`** — Highest-priority sentinel; clears destructive operations, credential exposure, and external writes (read-only)
78. **`@code-hygiene`** — Architecture auditor; file hygiene, duplication, script lifecycle, agent doc quality — CH-01 through CH-20+ (read-only)
79. **`@adversarial`** — Presupposition critic; challenges plans and hidden assumptions before execution (read-only)
80. **`@conflict-auditor`** — Detects logical inconsistencies across all output files; logs findings (read + log)
81. **`@conflict-resolution`** — Makes ACCEPT/REJECT/REVISE decisions on flagged conflicts (read + edit)
82. **`@cleanup`** — Removes stale artifacts; requires `@security` clearance; applies four safety checks before deletion (edit)
83. **`@agent-updater`** — Synchronizes agent documentation when project structure or content changes (edit)
84. **`@agent-refactor`** — Extracts shared data to reference files; enforces spec compliance (edit)
85. **`@repo-liaison`** — Manages cross-repository impact assessment, adjacent-repo updates, and orchestrator coordination (edit)
86. **`@git-operations`** — Handles commit/push, pull/merge/rebase, recovery, and conflict-resolution git workflows (edit)

---

## Workflows

Workflows are step sequences embedded in the generated Orchestrator agent. Every workflow terminates with an unconditional call to Workflow 11 (Final Check).

87. **Workflow 1 — Produce Deliverable** — Source review → Component Brief → production → quality audit → style audit → consistency check
88. **Workflow 2 — Revise Deliverable** — Revision → adversarial review → quality audit → consistency check → `@agent-updater` sync
89. **Workflow 3 — Technical Accuracy Audit** — Validate claims against source files; conditionally correct and re-audit
90. **Workflow 4 — Compile Final Output** — Format conversion → reference verification → final assembly → cleanup
91. **Workflow 5 — Consistency Review** — `@adversarial` challenge → cross-file consistency audit → technical validation → `@agent-updater`
92. **Workflow 6 — Documentation Maintenance** — Sync agent docs → refactor check → consistency audit
93. **Workflow 7 — Cleanup** — Identify stale artifacts → `@adversarial` review → `@security` clearance → delete → update docs
94. **Workflow 8 — Code Hygiene Audit** — CH-01–CH-20 audit → `@adversarial` guard before deletion plan → conditional cleanup/refactor
95. **Workflow 9 — Cross-Repository Coordination** — Impact assessment → approved updates → adjacent-repo writes (security-cleared) → consistency audit
96. **Workflow 10 — Plan Documentation & Review** — Plan status scan → pre-execution truth check via `@technical-validator` → surface blocked steps
97. **Workflow 10B — Work Summary Reporting** — Generate daily/weekly/monthly summaries from plan artifacts and git history, then audit them
98. **Workflow 11 — Final Check (Part A)** — Scan current plan's `steps.csv` for `pending`/`blocked` rows; create audited sub-plans for each
98. **Workflow 11 — Final Check (Part B)** — Scan `CHANGELOG.md` Known Issues, `tmp/by-week/YYYY-Www/` plan CSVs (legacy: `tmp/` root), and `git status` for at-large open issues; subject summaries to `@adversarial` + `@conflict-auditor`

---

## Governance Infrastructure

100. **Constitutional Rules** — Immutable rules encoded in the Orchestrator template: security clearance gates, code-hygiene before merge, conflict audit after multi-file sessions, adversarial review before irreversible changes
101. **Authority Hierarchy** — Explicit precedence ordering encoded in every generated Orchestrator (template library → schemas → pipeline source → placeholder conventions → implementation plan)
102. **Automatic `@agent-updater` Triggers** — Invoked at the close of Workflows 2, 3, and 5 after any knowledge-mutating operation
103. **`@adversarial` Guards in Audit Workflows** — `@adversarial` runs as step 1 of Workflow 5 and step 2 of Workflow 8 to prevent stale-assumption conclusions
104. **Pre-Execution Truth Check** — `@technical-validator` must verify factual claims in each plan step before it is marked `in_progress`
105. **Drift-as-Trigger** — An explicit trigger in `@agent-updater` trigger tables: drift detected by `--check` requires re-render and re-verify before the next workflow
106. **Cross-Repository Security Rule** — Any write to a repository other than `src/` must be assessed by `@repo-liaison` and cleared by `@security`

---

## Interoperability

107. **`convert` Module** — Direct format migration between framework outputs (`copilot-vscode` ↔ `copilot-cli` ↔ `claude`)
108. **`--convert-from` Flag** — Convert an existing agent team to a different framework format
109. **File Classification** — Auto-detect file role (agent, instruction, reference) for correct translation
110. **`interop` Module** — Canonical Agent Interface (CAI) normalization and compatibility pipeline
111. **`--interop-from` Flag** — Run the CAI interop pipeline against an existing team
112. **`bridge` Module** — Lightweight runtime compatibility bridge; generate bridge artifacts without regenerating sources
113. **`--bridge-from` Flag** — Generate bridge artifacts from a source canonical team
114. **Runtime Handoff Manifest** — `references/runtime-handoffs.json`; emitted when handoffs are extracted from non-VS Code adapters, consumed by bridge layers and external tooling

---

## Bridge Automation

115. **`run_daily_bridge_maintenance.sh`** — Daily warn-and-continue bridge refresh script for non-critical operations
116. **`bridge-maintenance.yml` CI Workflow** — GitHub Actions workflow for daily automated bridge maintenance
117. **`bridge-watchdog.yml` CI Workflow** — Staleness monitoring; creates deduplicated GitHub issues when drift is detected
118. **Bridge Staleness Detection** — Compare bridge artifacts against source team; surface divergence
119. **Deduplicated Issue Creation** — Watchdog suppresses duplicate GitHub issues for the same staleness event
120. **CI Fallback Mechanism** — Maintenance workflow continues with warnings on non-critical failures rather than aborting
121. **Snapshot Archive** — Pre-update snapshots stored in `references/plans/snapshots-*/` for reversible rollback

---

## Cross-Repository Support

122. **`@repo-liaison` Agent** — Manages cross-repository impact assessment, updates, and orchestrator coordination
123. **`references/adjacent-repos.md`** — Living record of adjacent repositories with changelog entries
124. **Protocol 1 — Impact Assessment** — Evaluate which adjacent repos are affected by a proposed change; produce an Impact Report
125. **Protocol 2 — Adjacent Updates** — Apply approved updates to neighboring project repos (requires `@security` clearance on each write)
126. **Protocol 3 — Orchestrator Coordination** — Formal coordination request between independent orchestrators managing different repositories
127. **`@repo-liaison` in Constitutional Rules** — Any write outside `src/` must be assessed by `@repo-liaison` before `@security` clearance can be granted
128. **`orchestrator-workflows.reference.md`** — Reference guide documenting all 12 workflows; kept in sync by `@agent-updater`

---

## Getting Started

### Essential Features

| Step | Feature | CLI / Entry Point |
|------|---------|-------------------|
| 1 | Create a project brief | Write `brief.json` or `brief.md` |
| 2 | Generate your team | `agentteams --description brief.json --project /path --framework copilot-vscode` |
| 3 | Review manual fills | Read the generated `SETUP-REQUIRED.md` |
| 4 | Enrich defaults | `agentteams --enrich` |
| 5 | Audit output | `agentteams --post-audit` |

### Advanced Features

| Feature | When to Use |
|---------|------------|
| `--migrate` | Upgrading a pre-fencing team to use `AGENTTEAMS:BEGIN/END` markers |
| `--update --merge` | Re-rendering a team after template updates without losing user edits |
| `--convert-from` | Migrating a team to a different AI framework |
| `--bridge-from` | Creating lightweight runtime compatibility artifacts without regenerating sources |
| `--check` (drift) | Detecting whether template or team-composition changes require re-render |
| `--scan-security` | Auditing generated files for credentials, PII, and unresolved placeholders |

### Governance Invocations

| Need | Invoke |
|------|--------|
| Destructive operation (file delete, bulk edit, external write) | `@security` |
| Code change touching shared utilities or agent docs | `@code-hygiene` |
| Multi-file session closing | `@conflict-auditor` |
| Plan with irreversible steps | `@adversarial` |
| Cross-repository write | `@repo-liaison` → `@security` |
