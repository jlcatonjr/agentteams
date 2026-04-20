# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Known Issues / Bugs

- ~~**BUG: `--update --merge` silently overwrites user-authored content below fences**~~ — **Fixed in this release.** See Added section below.

### Added

- **Safety: automatic backup before writes** — `build_team.py` now creates a timestamped backup of all agent files that will be overwritten before any `--overwrite`, `--merge`, or `--update` run. Backups are stored at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/` (excluded from git via `.gitignore`). New flags: `--no-backup` (suppress for CI), `--list-backups` (enumerate available backups), `--restore-backup [TIMESTAMP|latest]` (restore a backup). New public API: `emit.backup_output_dir()`, `emit.list_backups()`, `emit.restore_backup()`.
- **Bug fix: `--update --merge` now honours the `--merge` flag** — previously the `--update` code path hardcoded `overwrite=True` in `emit.emit_all()`, ignoring `--merge` entirely and silently destroying user-authored content below AGENTTEAMS fence markers (e.g. `adjacent-repos.md` Active Entries). The fix forwards `merge=args.merge` and sets `overwrite=not args.merge`.
- **Tests: 12 new tests in `tests/test_emit.py`** — covers `backup_output_dir` (empty dir, populated dir, selective, dry-run, no-backup-dir recursion), `list_backups` (empty, newest-first ordering), `restore_backup` (round-trip, missing-path error), and a regression test confirming `--merge` preserves user content below fences.
- **Governance: automatic `@agent-updater` triggers** — `@agent-updater` is now invoked at the close of Workflows 2 (Revise), 3 (Technical Accuracy Audit, when corrections were made), and 5 (Consistency Review, when issues found); ensures agent documentation is synchronized after every knowledge-mutating operation
- **Governance: `@adversarial` guard on audit workflows** — `@adversarial` now runs as step 1 of Workflow 5 (Consistency Review) before any audit conclusions are surfaced, and as step 2 of Workflow 8 (Code Hygiene Audit) before any deletion plan proceeds; prevents agents from acting on unchallenged stale assumptions
- **Governance: pre-execution truth check in Workflow 10 (Plan Review)** — `@technical-validator` must verify factual claims in each plan step's `inputs`, `outputs`, and `notes` against current on-disk state before the step is marked `in_progress`; unverified claims are surfaced to the user and block execution
- **Governance: Periodic Knowledge Re-verification protocol** — `agent-updater.template.md` and deployed `agent-updater.agent.md` now include a `Periodic Knowledge Re-verification` section defining when to re-run `--check`, how to validate plan facts with `@technical-validator`, and an escalation rule for mid-workflow drift detection
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
- Template library: 9 universal governance templates, 9+ domain archetype templates, 3 builder templates, 6 workstream expert-pattern templates
- JSON schemas: `project-description.schema.json` and `team-manifest.schema.json`
- Example project briefs: research, software, and data-pipeline projects
- `--self` mode: self-maintenance of the module's own agent team
- `--post-audit` mode: static + AI-powered conflict and presupposition review
- `--update` / `--prune` mode: incremental re-rendering with manual value preservation
