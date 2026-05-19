# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### P0 — Drift trust + P3 — Update delivery gating

- **P0: `FINGERPRINT_ALGO_VERSION` constant + algo-version field in build-log** — `agentteams/drift.py` now defines a module-level `FINGERPRINT_ALGO_VERSION` constant (currently `1`). `_write_run_log` writes a `fingerprint_algo_version` field alongside `manifest_fingerprint`. A bumped algo version forces a one-shot re-promotion of the unchanged set with reason `"fingerprint algo version bumped"`; pre-version build-logs (missing the field) are treated as legacy — only an actual fingerprint mismatch promotes. The constant is pinned by `test_fingerprint_algo_version_pinned` to force PR review on any future bump.
- **P0: observable baseline self-heal on `--update`** — when `--update` sees `manifest_changed` but content-aware refinement demotes every fingerprint-only promotion (and there is no template/structural/team-membership drift, no `added_files`, no `removed_files`), `build_team.py` now prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).` The heal *write* is implicit via the existing `_write_run_log` call at the end of `--update` — the print just makes the convergence observable. Heal is suppressed under `--dry-run` and never fires when `removed_files` is non-empty (resolution belongs to `--update --prune`).
- **P0: `--check` Option C render-faithful reconciliation (D1)** — `--check` now mirrors what `--update` would write: when `sdreport.manifest_changed AND any(_reason in drift._MANIFEST_PROMOTION_REASONS)`, `--check` renders the full team through the same `render.render_all → adapter post-processing → finalize_output_path` pipeline `--update` uses and runs `refine_manifest_promotion` against the same `_content_matches` closure. Structural-diff output is now printed under the same `has_changes` condition `--update` uses (not just on added/removed). `--check` and `--update --dry-run` now agree on the post-refinement drifted set (pinned by `test_check_parity_with_update_dry_run`).
- **P3: delivery receipt** — `build_team --update` now writes a delivery receipt at `<output_dir>/references/delivery-receipt.json` after the build-log, inside the same `not args.dry_run and result.success` block (the "heal first, attest second" order). The receipt is schema-validated (`schemas/delivery-receipt.schema.json`) and includes `artifact_type: delivery-receipt` (NOT `schema_version`, so build-log readers do not accidentally treat a receipt as a baseline), `manifest_fingerprint`, and `fingerprint_algo_version`. The receipt is excluded from drift artifacts by construction (not in `output_files_map`, `template_hashes`, or `file_hashes`) and is never read by `--check` or `--update`. See `docs_src/delivery-procedure.md` for verification procedures.
- **Docs: delivery procedure guide** — new `docs_src/delivery-procedure.md` documents receipt semantics (attestation, not baseline), CI verification recipes, and the explicit "what the receipt does not prove" contract. Registered under the Guides section of `mkdocs.yml`.

### Contract notes (read before depending on)

- **D1**: `--check` rendering is gated; outside the fast-path predicate it short-circuits. The structural-diff print scope now matches `--update` (`has_changes`).
- **D2**: P3 enforcement is doc + receipt emission. No CLI flag added; no wrapper command added.
- **D3**: Receipt path is `references/delivery-receipt.json`. Top-level discriminator is `artifact_type: delivery-receipt`. Receipt schema version is `receipt_schema_version: "1.0"` — distinct from build-log `schema_version`.
- **M2**: First `--update` after upgrade rewrites the build-log with the current `fingerprint_algo_version` (the heal). Convergence is asserted by `test_stale_fingerprint_converges_in_two_updates`.

### Governance

- **History rewrite: VisualKnowledge references removed from commit history** — all commit messages and tracked file content matching `visualknowledge`, `/visualknowledge/`, and `vk-[a-z0-9-]+` patterns were replaced with `REDACTED_REPO` / `REDACTED_SERVICE` using `git filter-repo`. Pre-rewrite HEAD: `10b8bfc`. Post-rewrite HEAD: `b67c514`. Mirror backup retained at `tmp/by-week/2026-W19/rewrite-backups/agentteams.mirror.20260504-160919.git`. Post-rewrite verification: MSG_HITS=0, TRACKED_HITS=0.
- **Commentary scope rule** — added constitutional rule to `.github/copilot-instructions.md`, `orchestrator.agent.md`, `agent-updater.agent.md`, and `work-summarizer.agent.md`: VisualKnowledge repository operational updates must not appear in AgentTeams commit/PR notes, comments, or work summary narrative unless the entry documents a direct, material change to files inside this repository.

### Known Issues / Bugs

- ~~**BUG: `--update --merge` silently overwrites user-authored content below fences**~~ — **Fixed in this release.** See Added section below.
- **KNOWN ISSUE: `.agentteams-backups/` directories are committed in managed repos that lack a `.gitignore` rule** — `build_team.py` does not auto-write a `.gitignore` rule for the backup directory in managed repos. Repos that have no pre-existing rule will commit rollback backup snapshots to git history. Affected repos should add `.github/agents/.agentteams-backups/` to `.gitignore` and run `git rm -r --cached .github/agents/.agentteams-backups/`. A systemic fix (auto-write the rule on init/update) is tracked for the agentteams pipeline. See `tmp/by-week/2026-W19/groupb-backup-dir-cleanup-2026-05-04.plan.md`.

### Added

- **Governance: explicit agent-documentation trigger for audits** — Workflow 6 (Documentation Maintenance) trigger phrase list now includes "Agent documentation changed", and `@agent-updater` has a new Trigger Conditions row requiring repository change census, doc sync, then `@adversarial` + `@conflict-auditor` handoff before closeout whenever agent documentation is updated. Applied to both deployed `.github/agents/` files and `agentteams/templates/universal/` so generated teams inherit the trigger.

- **Reference: Unix Philosophy Mapping for Code Hygiene Rules** — added `agentteams/templates/domain/unix-philosophy-mapping.template.md` and integrated into build pipeline. Each generated team includes `references/unix-philosophy-mapping.reference.md` mapping rules (CH-01 through CH-23) to Unix design principles. Three-tier classification: Tier 1 (foundational), Tier 2 (aligned), Tier 3 (project-specific). See audit report `tmp/by-week/2026-W20/unix-philosophy-mapping-audit-revisions.md`.

- **Security: post-production audit hardening** — added the post-production auditor template, closure-gate schemas, and supporting docs/tests/build updates alongside the generated site and examples sync.
- **Docs: API reference alignment for post-production auditing** — updated `docs_src/api-reference/analyze.md`, `docs_src/api-reference/index.md`, and `docs_src/api-reference/feature-inventory.md` to reflect current archetype selection behavior and release-availability wording.
- **Bridge automation procedures** — added `scripts/run_daily_bridge_maintenance.sh` for non-critical warn-and-continue bridge refresh/check operations, plus `.github/workflows/bridge-maintenance.yml` (daily maintenance) and `.github/workflows/bridge-watchdog.yml` (staleness monitoring with deduplicated issue creation).

- **Safety: automatic backup before writes** — `build_team.py` now creates a timestamped backup of all agent files that will be overwritten before any `--overwrite`, `--merge`, or `--update` run. Backups are stored at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/` (callers are responsible for adding a `.gitignore` rule to exclude backups from git — the tool does not write this rule automatically). New flags: `--no-backup` (suppress for CI), `--list-backups` (enumerate available backups), `--restore-backup [TIMESTAMP|latest]` (restore a backup). New public API: `emit.backup_output_dir()`, `emit.list_backups()`, `emit.restore_backup()`.
- **Bug fix: `--update --merge` now honours the `--merge` flag** — previously the `--update` code path hardcoded `overwrite=True` in `emit.emit_all()`, ignoring `--merge` entirely and silently destroying user-authored content below AGENTTEAMS fence markers (e.g. `adjacent-repos.md` Active Entries). The fix forwards `merge=args.merge` and sets `overwrite=not args.merge`.
- **Tests: 12 new tests in `tests/test_emit.py`** — covers `backup_output_dir` (empty dir, populated dir, selective, dry-run, no-backup-dir recursion), `list_backups` (empty, newest-first ordering), `restore_backup` (round-trip, missing-path error), and a regression test confirming `--merge` preserves user content below fences.
- **Governance: automatic `@agent-updater` triggers** — `@agent-updater` is now invoked at the close of Workflows 2 (Revise), 3 (Technical Accuracy Audit, when corrections were made), and 5 (Consistency Review, when issues found); helps keep agent documentation synchronized after knowledge-mutating operations in those workflows
- **Governance: `@adversarial` guard on audit workflows** — `@adversarial` now runs as step 1 of Workflow 5 (Consistency Review) before any audit conclusions are surfaced, and as step 2 of Workflow 8 (Code Hygiene Audit) before any deletion plan proceeds; prevents agents from acting on unchallenged stale assumptions
- **Governance: pre-execution truth check in Workflow 10 (Plan Review)** — `@technical-validator` must verify factual claims in each plan step's `inputs`, `outputs`, and `notes` against current on-disk state before the step is marked `in_progress`; unverified claims are surfaced to the user and block execution
- **Governance: Workflow 11 — Final Check** added to `orchestrator.template.md` as the terminal step of every workflow (Workflows 1–10 each close with an unconditional `→ Invoke Workflow 11: Final Check` step). Final Check has two parts: Part A scans the current plan's `steps.csv` for `pending`/`blocked` rows and creates audited sub-plans for each; Part B scans `CHANGELOG.md` Known Issues, `tmp/` CSVs, and `git status` for repo at-large open issues, summarises each, and subjects summaries to `@adversarial` + `@conflict-auditor` before surfacing to the user. The deployed `agentteams` orchestrator defines Final Check as Workflow 11.
- **Infrastructure: `available_workflows` section now FENCED** — `orchestrator.template.md` wraps the Available Workflows block in `<!-- AGENTTEAMS:BEGIN available_workflows v=1 -->` / `<!-- AGENTTEAMS:END available_workflows -->` markers. The USER-EDITABLE gap between the `routing_table_rows` END marker and the `available_workflows` BEGIN marker is the permanent home for project-specific rules. This lets `--update --merge` propagate workflow changes (including new Final Check steps) while preserving project-specific rules (e.g. BBB IDs, conflict prefixes, domain agent lists) during fenced updates.
- **Protocol: Update Deployment Protocol** added to `agent-updater.template.md` (and deployed to all `agent-updater.agent.md` files via batch update). Documents the required protocol for every `--update`/`--update --merge` run: pre-update dry-run, automatic backup verification, git pre/post diff capture, post-update outside-fence deletion analysis (OK/WARN/ERROR classification), WARN review gate before commit, non-git repo backup-vs-current diff path, and batch operation requirements (`batch_update.py` writes per-repo `.diff` files to `tmp/diffs/` and a summary CSV). Propagated to all 19 git repos and 2 non-git repos via `batch_update.py`.
- **Infrastructure: `tmp/inject_fences.py`** — new utility script that adds `AGENTTEAMS:BEGIN/END available_workflows` fence markers to orchestrator.agent.md files that have `routing_table_rows` fences but are missing the `available_workflows` fence. Used to prepare all 17 repos for `--update --merge` without duplication risk. Also patches section manifest comments.
- **Tests: snapshot comparison now excludes live-data files** — `test_snapshot_comparison` skips `security-vulnerability-watch.reference.md` and `security.agent.md` from comparison (both contain live CISA/NVD/EPSS threat intelligence refreshed on every pipeline run; non-deterministic).
- **Governance: drift-as-trigger** — a new trigger row in `agent-updater` trigger tables: "Drift detected by `--check`" — agents operating on stale knowledge of file structure, agent slugs, or counts must re-render and re-verify before the next workflow executes
- **Infrastructure: Workflow 9 (Cross-Repository Coordination)** added to `orchestrator-workflows.reference.md`; previously documented only in the orchestrator agent file
- **Infrastructure: snapshot archive** — pre-update snapshots of all patched agent files saved to `references/plans/snapshots-2026-04-17/` for reversible rollback

### Changed

- Workflow 2 in `orchestrator.template.md` and deployed `orchestrator.agent.md`: step 8 added (`@agent-updater` sync)
- Workflow 3: step 6 added (conditional `@agent-updater` when corrections made)
- Workflow 5: steps renumbered; `@adversarial` inserted as step 1; `@agent-updater` added as step 7
- Workflow 8: steps renumbered; `@adversarial` inserted as step 2; step references updated
- Workflow 10: step 3 added (pre-execution truth check via `@technical-validator`); remaining steps renumbered
- `orchestrator.agent.md` routing table: resolved unresolved `{MANUAL:STYLE_REFERENCE_PATH}` and `{MANUAL:REFERENCE_DB_PATH}` tokens with accurate N/A annotations
- `agent-updater.agent.md`: resolved unresolved `{MANUAL:REFERENCE_DB_PATH}` and `{MANUAL:STYLE_REFERENCE_PATH}` tokens in Change-to-Agent Mapping table non-destructive section-fencing merge mode — updates only `AGENTTEAMS:BEGIN/END`-fenced regions in existing agent files; preserves all user-authored content outside fence boundaries; skips legacy files (no fence markers) with an advisory warning
- `--migrate` flag: one-step legacy fencing migration — creates a `pre-fencing-snapshot` git tag at HEAD, runs `--overwrite` to regenerate all agent files with fenced templates, and prints a quality-audit checklist
- `--revert-migration` flag: undo a `--migrate` run — runs `git reset --hard pre-fencing-snapshot` in the project directory and deletes the snapshot tag
- `--enrich` flag: scan generated files for default template elements and apply context-aware auto-enrichment (rule-based + notebook scanning + tool catalog); exports `references/defaults-audit.csv`
- `--auto-correct` flag: invoke standalone `copilot` CLI to repair post-audit findings, then rerun the audit to confirm
- `--scan-security` flag: proactive scan for PII paths, credential patterns, and unresolved placeholders in generated agent files
- `--security-offline`, `--security-max-items`, `--security-no-nvd` flags: control live security intelligence fetching in generated security-reference files
- Section-fencing system: `AGENTTEAMS:BEGIN/END` fence markers, section manifest convention, `FENCE-CONVENTIONS.md` specification, and 11 instrumented templates
- `enrich` package: auto-enrichment pipeline (`_enrich.py`, `_fills.py`, `_notebooks.py`, `_tools.py`, `_models.py`, `_audit.py`)
- `security_refs` module: live CVE/CISA-KEV/EPSS intelligence rendering into generated security reference files
- Team topology graph (`graph` module): directed graph inference with Mermaid, DOT, JSON, and Markdown output; `references/pipeline-graph.md` generated on every emit
- `drift` module additions: `detect_user_customizations()` advisory surface for `--merge`; structural diff (`compute_structural_diff()`) for `--update`
- `man` module: auto-generated man page from CLI flags (`agentteams.1`)
- 9 additional tests in `tests/test_migrate.py` covering `--migrate`/`--revert-migration` round-trips, failure modes, argv rewriting, and tag lifecycle

## [0.1.0] - 2026-04-15

### Added

- **Reference: Unix Philosophy Mapping for Code Hygiene Rules** — added `agentteams/templates/domain/unix-philosophy-mapping.template.md` and integrated into build pipeline. Each generated team includes `references/unix-philosophy-mapping.reference.md` mapping rules (CH-01 through CH-23) to Unix design principles. Three-tier classification: Tier 1 (foundational), Tier 2 (aligned), Tier 3 (project-specific). See audit report `tmp/by-week/2026-W20/unix-philosophy-mapping-audit-revisions.md`.

- `ingest` module: load project descriptions from `.json` or `.md` briefs; scan existing project directories to supplement missing fields
- `analyze` module: classify project type, select agent archetypes, detect tool agents, build team manifest
- `render` module: resolve auto and manual placeholders in agent templates; compute template hashes for drift detection
- `emit` module: write rendered agent files to disk with dry-run and overwrite-protection support
- `drift` module: detect content drift (template hash comparison) and structural drift (team composition changes) against build-log
- `scan` module: proactive security scan for PII paths, credentials, and unresolved placeholders
- `audit` module: post-generation static audit plus optional AI-powered review via `copilot` CLI
- `remediate` module: auto-correction support via standalone Copilot CLI after audit findings
- `graph` module: directed graph inference for agent team topology; outputs Mermaid, DOT, JSON, and Markdown
- `frameworks` package: `copilot-vscode`, `copilot-cli`, and `claude` framework adapters
- `build_team.py` CLI: 16-flag command-line interface wiring all pipeline stages
- Template library at the 0.1.0 release: 9 universal governance templates, 9+ domain archetype templates, 3 builder templates, 6 workstream expert-pattern templates
- JSON schemas: `project-description.schema.json` and `team-manifest.schema.json`
- Example project briefs: research, software, and data-pipeline projects
- `--self` mode: self-maintenance of the module's own agent team
- `--post-audit` mode: static + AI-powered conflict and presupposition review
- `--update` / `--prune` mode: incremental re-rendering with manual value preservation
