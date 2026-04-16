# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--merge` flag: non-destructive section-fencing merge mode — updates only `AGENTTEAMS:BEGIN/END`-fenced regions in existing agent files; preserves all user-authored content outside fence boundaries; skips legacy files (no fence markers) with an advisory warning
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
