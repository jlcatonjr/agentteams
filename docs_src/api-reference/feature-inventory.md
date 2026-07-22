---
title: Feature Inventory
description: Comprehensive enumeration of all AgentTeams capabilities, organized by category
---

# AgentTeams — Feature Inventory

**Version baseline:** 0.1.0 (Released 2026-04-15)

Features below are grouped by capability area. This inventory may include capabilities currently present on
`main` that are not yet part of a tagged release. Release-specific availability should be verified against
`CHANGELOG.md` and repository tags.

---

## Summary

--8<-- "docs_src/assets/feature-summary-table.md"

---

## Artifact Selection Matrix

Use this matrix when deciding which module/artifact to rely on for a specific operational goal.

| Goal | Primary Module | Primary Artifact | Typical Trigger | Notes |
|------|----------------|------------------|-----------------|-------|
| Detect template/structure drift | `drift` | `references/build-log.json` | `--check`, `--update` | Structural + template fingerprint analysis |
| Update many workspaces at once | `fleet` | `<DIR>/.agentteams-fleet/<run-id>/report.json` | `--fleet DIR --update --merge` | Git-commit snapshot per workspace + `git diff` content audit; non-destructive (merge-only); dry-run by default, `--yes` to apply |
| Verify behavioral conformance | `eval_suite` + `behavioral_drift` | `references/eval-suite.json` + trajectory payload | Post-run behavioral validation | Neutral suite + runtime trajectory comparison |
| Fast historical retrieval | `memory_index` | `references/memory-index.json` | `--refresh-index`, `--query-index` | Use lexical first, vector when thematic recall is needed |
| Cost/capability routing policy | `model_routing` | `references/model-routing.json` | `--cost-routing` | Tier-role contract, concrete models resolved downstream |
| Cross-framework eval execution | `eval_adapters` | Generated adapter output (Inspect module / OpenAI Evals JSON) | Adapter export step | Converts neutral eval-suite into framework-specific eval assets |
| Security intelligence refresh | `security_refs` | `references/security-vulnerability-watch.reference.md` + `.json` | Normal generate/update path (unless offline) | Live-data artifacts; intentionally non-deterministic |
| Rollback-ready update safety | `emit` backup APIs | `.agentteams-backups/<timestamp>/` + `_manifest.json` | `--update`, `--merge`, `--overwrite` | Supports restoration and backup integrity checks |

---

## Core Pipeline

### Team Generation

1. **Agent Teams Module** — Generate a complete, coordinated AI agent team from a single project description file
2. **Four-Tier Agent Architecture** — Orchestrator → Governance Agents → Domain Agents → Workstream Experts
3. **Project Description Ingestion** — Load project briefs from `.json` or `.md` format
4. **Project Directory Scanning** — Scan an existing project directory to supplement missing brief fields
5. **Project Type Classification** — Infer the deliverable type from the project goal
6. **Agent Archetype Selection** — Select the appropriate archetype set from the schema-supported archetype catalog, including contextual post-production auto-selection
7. **Team Manifest Generation** — Build complete team composition metadata
8. **Template Placeholder Resolution** — Resolve `{AUTO:token}` and `{MANUAL:token}` placeholders

### Core Pipeline Modules

9. **`ingest` Module** — Load and normalize project descriptions; parse JSON/Markdown; scan directories
10. **`analyze` Module** — Classify project type; select archetypes (including contextual post-production auto-selection for any task domain and manual `selected_archetypes` override support); detect operational tool docs; build manifest
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

Two additional first-class adapters extend this set:

- **`goose` Adapter** *(beta)* — Block / AAIF Goose recipe YAML (`.goose/recipes/*.yaml`, schema `1.0.0`); orchestrator delegation encoded natively as `sub_recipes` (deeper edges become `summon` `load(...)`); team brief written to the repo-root `AGENTS.md` plus a `.goosehints` integrator. Also a valid `--convert-from` and `--bridge-from` **target** (not an interop target). **Beta** — generate/convert/bridge supported, interop planned; adapter API not yet under the stability contract. See [`frameworks`](frameworks.md).
- **`agents-md` Adapter** — Cross-tool **AGENTS.md** standard (AAIF / Linux Foundation); emits a single framework-neutral repo-root `AGENTS.md` (read by ~10 AI coding tools) plus per-specialist detail under `.agents/`. **Generate-only** (not a convert/interop/bridge target).

### CLI

26. **`agentteams` / `build_team.py` CLI** — Command-line interface wiring all pipeline stages
27. **`--description`** — Path to project brief (`.json` or `.md`)
28. **`--project`** — Target project directory
29. **`--framework`** — Output framework: `copilot-vscode`, `copilot-cli`, `claude`, `goose`, or `agents-md`
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
64. **`--update --merge` Canonical Mode** — Incremental re-render that preserves manual placeholder values, preserves user-authored content outside fences, and restores expected standard outputs missing on disk
65. **`--prune` Mode** — Remove stale agent components from an existing team
66. **`--auto-correct` Flag** — Invoke Copilot CLI to repair post-audit findings; reruns audit to confirm
67. **`--scan-security` Flag** — Proactive scan for PII paths, credential patterns, unresolved placeholders
68. **`available_workflows` Fenced Section** — Workflow documentation fenced so `--update --merge` propagates future changes while preserving project-specific rules
69. **Update Deployment Protocol & Fleet Update (`--fleet DIR`)** — A documented multi-step update procedure (dry-run, backup verify, git diff capture, outside-fence analysis (OK/WARN/ERROR), WARN review gate), now **productized as the `--fleet` command** that runs it across **every** workspace under a directory: discovers `.github/agents/` and `.claude/` targets, snapshots each git workspace via a commit, applies `--update --merge` in-process, then classifies the `git diff` by content signals (shrink Notices, USER-EDITABLE deletions). Non-destructive (merge-only); dry-run by default, `--yes` to apply; report under `<DIR>/.agentteams-fleet/<run-id>/`. See [`fleet`](fleet.md).

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
78. **`@code-hygiene`** — Architecture auditor; file hygiene, duplication, script lifecycle, agent doc quality — invariant CH-01 through CH-20 plus project extension rules CH-21–CH-24 (Testing, Type Safety, Defensive Programming; CH-24: `try`/`except`/`finally` is a last resort — encode expected conditions in dictionaries/guards and fail hard, reinforcing CH-23 Fail Fast) (read-only)
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

Workflows are step sequences embedded in the generated Orchestrator agent. Every built-in workflow terminates with an unconditional call to Workflow 11 (Final Check). Optional user-added workflow extensions must explicitly include the same terminal handoff.

87. **Workflow 1 — Produce Deliverable** — Source review → Component Brief → production → quality audit → style audit → consistency check
88. **Workflow 2 — Revise Deliverable** — Revision → adversarial review → quality audit → consistency check → `@agent-updater` sync
89. **Workflow 3 — Technical Accuracy Audit** — Validate claims against source files; conditionally correct and re-audit
90. **Workflow 4 — Compile Final Output** — Format conversion → reference verification → final assembly → cleanup
91. **Workflow 5 — Consistency Review** — `@adversarial` challenge → cross-file consistency audit → technical validation → `@agent-updater`
92. **Workflow 6 — Documentation Maintenance** — Sync agent docs → refactor check → consistency audit
93. **Workflow 7 — Cleanup** — Identify stale artifacts → `@adversarial` review → `@security` clearance → delete → update docs
94. **Workflow 8 — Code Hygiene Audit** — CH-01–CH-24 audit (CH-01–CH-20 invariant + CH-21–CH-24 project extensions) → `@adversarial` guard before deletion plan → conditional cleanup/refactor
95. **Workflow 9 — Cross-Repository Coordination** — Impact assessment → approved updates → adjacent-repo writes (security-cleared) → consistency audit
96. **Workflow 10 — Plan Documentation & Review** — Plan status scan → pre-execution truth check via `@technical-validator` → surface blocked steps
97. **Workflow 10B — Work Summary Reporting** — Generate daily/weekly/monthly summaries from plan artifacts and git history, then audit them
98. **Workflow 10C — Post-Production Audit Verification (Optional)** — User-editable extension workflow for outcome verification after implementation claims; runs `@post-production-auditor` + adversarial/conflict checks; blocks closeout on `FAIL`/`INCONCLUSIVE`
99. **Workflow 11 — Final Check (Part A)** — Scan current plan's `steps.csv` for `pending`/`blocked` rows; create audited sub-plans for each
100. **Workflow 11 — Final Check (Part B)** — Scan `CHANGELOG.md` Known Issues, `tmp/by-week/YYYY-Www/` plan CSVs (legacy: `tmp/` root), and `git status` for at-large open issues; subject summaries to `@adversarial` + `@conflict-auditor`

---

## Governance Infrastructure

101. **Constitutional Rules** — Immutable rules encoded in the Orchestrator template: security clearance gates, code-hygiene before merge, conflict audit after multi-file sessions, adversarial review before irreversible changes
102. **Authority Hierarchy** — Explicit precedence ordering encoded in every generated Orchestrator (template library → schemas → pipeline source → placeholder conventions → implementation plan)
103. **Automatic `@agent-updater` Routing in Documentation-Impact Workflows** — Routed in Workflows 1, 2, 3, 5, 6, 7, 8, and 9 when knowledge-mutating or coordination changes occur
104. **`@adversarial` Guards in Audit Workflows** — `@adversarial` runs as step 1 of Workflow 5 and step 2 of Workflow 8 to prevent stale-assumption conclusions
105. **Pre-Execution Truth Check** — `@technical-validator` must verify factual claims in each plan step before it is marked `in_progress`
106. **Drift-as-Trigger** — An explicit trigger in `@agent-updater` trigger tables: drift detected by `--check` requires re-render and re-verify before the next workflow
107. **Initialization-as-Trigger** — First successful team generation is an explicit lifecycle trigger: it establishes the baseline inventory and trigger corpus used by future update and drift logic
108. **Update Lifecycle Trigger Contract** — Canonical `--update --merge` runs must reconcile drift, emit newly required files, preserve manual values, and preserve user-authored content outside fenced regions
109. **Missing Expected Output Recovery Trigger** — During update, absent expected standard outputs are treated as drift and must be restored even if template hashes are unchanged
110. **Cross-Repository Security Rule** — Any write outside this project's configured primary output directory must be assessed by `@repo-liaison` and cleared by `@security`
111. **User-Editable Gap Safety Pattern** — Optional routing rows and optional workflow extensions (such as 10C) must be added outside FENCED sections so `--update --merge` does not force-propagate them to teams that do not include the required agent
112. **Post-Production Closure Gate Artifacts** — Optional post-production audits can emit `closure_gate_status.json`, `capability_check.json`, and `decision_replay_packet.json` to support fail-closed closeout decisions and replay-auditability

---

## Interoperability

113. **`convert` Module** — Direct format migration between framework outputs (`copilot-vscode` ↔ `copilot-cli` ↔ `claude`), and to `goose` (emits recipes + repo-root `AGENTS.md` + `.goosehints`; delegation wired from `copilot-vscode` sources, flat from `claude`/`copilot-cli`)
114. **`--convert-from` Flag** — Convert an existing agent team to a different framework format (targets: `copilot-vscode`, `copilot-cli`, `claude`, `goose`)
115. **File Classification** — Auto-detect file role (agent, instruction, reference) for correct translation
116. **`interop` Module** — Canonical Agent Interface (CAI) normalization and compatibility pipeline (`copilot-vscode` / `copilot-cli` / `claude` targets; `goose` is refused — the CAI does not carry the handoff graph Goose needs, so use `--convert-from` instead)
117. **`--interop-from` Flag** — Run the CAI interop pipeline against an existing team
118. **`bridge` Module** — Lightweight runtime compatibility bridge; generate bridge artifacts without regenerating sources (targets include `goose` — writes fenced `AGENTS.md` / `.goosehints` / `.goose/README.md` pointers)
119. **`--bridge-from` Flag** — Generate bridge artifacts from a source canonical team
120. **Runtime Handoff Manifest** — `references/runtime-handoffs.json`; emitted when handoffs are extracted from non-VS Code adapters, consumed by bridge layers and external tooling

---

## Bridge Automation

121. **`run_daily_bridge_maintenance.sh`** — Daily bridge refresh/check script that halts the run when the security-maintenance preflight fails and continues with warnings only for non-critical bridge refresh/check subtasks
122. **`bridge-maintenance.yml` CI Workflow** — GitHub Actions workflow for daily automated bridge maintenance
123. **`bridge-watchdog.yml` CI Workflow** — Staleness monitoring; creates deduplicated GitHub issues when drift is detected
124. **Bridge Staleness Detection** — Compare bridge artifacts against source team; surface divergence
125. **Deduplicated Issue Creation** — Watchdog suppresses duplicate GitHub issues for the same staleness event
126. **CI Fallback Mechanism** — Bridge refresh/check subtasks continue with warnings, but security-maintenance preflight failures abort the workflow
127. **Snapshot Archive** — Pre-update snapshots stored in `references/plans/snapshots-*/` for reversible rollback

---

## Cross-Repository Support

128. **`@repo-liaison` Agent** — Manages cross-repository impact assessment, updates, and orchestrator coordination
129. **`references/adjacent-repos.md`** — Living record of adjacent repositories with changelog entries
130. **Protocol 1 — Impact Assessment** — Evaluate which adjacent repos are affected by a proposed change; produce an Impact Report
131. **Protocol 2 — Adjacent Updates** — Apply approved updates to neighboring project repos (requires `@security` clearance on each write)
132. **Protocol 3 — Orchestrator Coordination** — Formal coordination request between independent orchestrators managing different repositories
133. **`@repo-liaison` in Constitutional Rules** — Any write outside `src/` must be assessed by `@repo-liaison` before `@security` clearance can be granted
134. **`orchestrator-workflows.reference.md`** — Reference guide documenting the baseline workflow set plus optional extension workflows (for example 10C when configured); kept in sync by `@agent-updater`

---

## Drift Trust & Delivery Gating

135. **`FINGERPRINT_ALGO_VERSION` Constant** — Module-level integer in `agentteams.drift` recording the version of the manifest-fingerprint algorithm; recorded into `build-log.json` on every write and consulted on `--check` / `--update` to migrate existing teams when fingerprint semantics change
136. **Algo-Bump Migration Mechanism** — `compute_structural_diff` detects `fingerprint_algo_version` mismatch between the stored build-log and the current module value, sets `manifest_changed`, and promotes affected files with `_reason = "fingerprint algo version bumped"` for a one-shot re-evaluation; legacy logs missing the field do not trigger promotion on their own
137. **Observable Baseline Self-Heal on `--update`** — When an `--update` run finds no material drift but the recorded fingerprint or algo version is stale, the build-log baseline is refreshed and the run prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).`; heal occurs before delivery-receipt attestation
138. **`refine_manifest_promotion` Render-Faithful Reconciliation** — Pure public function in `agentteams.drift` that demotes fingerprint-only promotions whose rendered content matches disk byte-for-byte; caller supplies a `content_matches(path)` closure; preserves `manifest_changed` as telemetry
139. **`--check` Option C Render-Faithful Mode** — When the structural diff sets `manifest_changed` with a manifest-promotion reason, `--check` runs the full render pipeline (`render.render_all` → framework adapter → handoff/graph append) and applies `refine_manifest_promotion` against a content-match closure mirroring `--update`'s, eliminating false-positive drift signals
140. **`--check` / `--update --dry-run` Parity Contract** — `--check` and `--update --dry-run` report the same `has_changes` set for the same inputs; enforced by `tests/test_integration.py::test_check_parity_with_update_dry_run`
141. **Delivery Receipt Artifact** — `references/delivery-receipt.json` written after every successful `--update` (non-dry-run) recording `artifact_type`, `receipt_schema_version`, `delivered_at`, `project_name`, `framework`, `manifest_fingerprint`, and `fingerprint_algo_version`; heal-first-attest-second order ensures the receipt records the post-heal state; write failures warn on stderr but do not fail the run; receipt file is excluded from drift detection
142. **`schemas/delivery-receipt.schema.json`** — Draft-07 JSON Schema for the delivery receipt; `additionalProperties: false`; top-level discriminator is `artifact_type` (const `"delivery-receipt"`); `receipt_schema_version` const `"1.0"`
143. **`docs_src/delivery-procedure.md` Operator Guide** — Documents the heal/attest order, receipt schema, and operator procedure for verifying a delivered update; registered under Guides in `mkdocs.yml`

---

## Retrieval & Review-Time Utilities

144. **Confidence-Tiered Retrieval** — `memory_index.query_index()` and `code_index.query_partition()`/`query_partitions()` hits carry a `confidence` field (`"reliable"` / `"candidate"` / `"weak"`) computed from `score` against per-strategy thresholds, printed alongside `score=` by `--query-index`/`--query-code`. Replaces threshold-interpretation prose duplicated across 6 templates (`conflict-resolution`, `conflict-auditor`, `quality-auditor`, `research-analyst`, `retrieval-integrator`, `tool-doc-researcher`). See [`memory-index`](memory-index.md), [`code-index`](code-index.md).
145. **`agentteams.scan` Review-Time Invocability** — `verdict_for_findings()` (`HALT` / `CONDITIONAL_PASS` / `PASS`) and a `python -m agentteams.scan <path>` entrypoint let a shell-only runtime invoke the deterministic PII/credential scanner at review time, not only at generation time via `--scan-security`. `security.template.md` Rules S-1/S-8 cite it as the preferred check. See [`scan`](scan.md).
146. **`session_scan` Module** — `scan_repo_issues()` consolidates the orchestrator's Workflow 11 Part B closeout scan (CHANGELOG "Known Issues", plan-steps pending/blocked rows, `git status` anomalies) into one call, with a `python -m agentteams.session_scan` entrypoint. See [`session_scan`](session_scan.md).

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
