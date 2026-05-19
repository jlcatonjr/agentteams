# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### P4 — Verification environment & reproducibility preflight

- **New preflight script: `scripts/verify-env.py`** — asserts the declared minimum Python (≥3.11) and `git` (≥2.23) versions before the test suite or any pipeline command runs; emits a structured failure mode (human-readable or `--json`) with a remediation hint pointing at `docs_src/verification-environment.md`. Exit codes are explicit: `0` pass, `1` precondition unmet, `2` unexpected error. The check is import-free of the `agentteams` package so it runs on a bare interpreter and fails loudly *before* `pip install -e .`.
- **New doc: `docs_src/verification-environment.md`** — declares the preconditions matrix, platform notes (macOS NFD vs Linux NFC; `git ls-files -z --literal-pathspecs` contract), and the procedure for extending the preflight. Registered in `mkdocs.yml` under Guides; linked from the README install section.
- **CI matrix expansion** — `.github/workflows/ci.yml` now runs the full `{python 3.11, 3.12} × {ubuntu-latest, macos-latest}` matrix with the preflight as the first step (fail-fast). The macOS leg keeps the unicode/path-normalization guarantee honest (the same defect class P2 addressed). The RSR1 lint guard (`scripts/check-durable-tmp-refs.sh`) runs on the Linux leg only (bash-only; redundant on macOS for the same allowlist).
- **Test coverage** — `tests/test_verify_env.py` exercises 13 cases: minimum-version pass, above-minimum pass, below-minimum failure (Python + git), missing-git failure, JSON mode, quiet mode, and the contract that the current repo environment must satisfy the preflight (regression guard against accidental floor bumps).

### P5 — Downstream redelivery procedure (generator-side close-out)

- **`docs_src/delivery-procedure.md`: new "Dry-run redelivery to a downstream repo" section** — documents the six-step procedure operators run when delivering an `agentteams build_team --update` to a downstream consumer (snapshot → throwaway dry-run → classify diff → cross-repo gate → real delivery → verify). Distinguishes **real drift** (generator output changed) from **reorg overlap** (downstream-only file moves) — the same diff-classification failure mode P5 identified in the hayekAI repo. Explicitly cites that any actual write to the downstream repo routes through `@repo-liaison` (Workflow 9) and requires `@security` clearance; the generator never writes outside its own repository.
- **Scope of generator-side close-out** — the generator-side preconditions (P2 cross-ref fixes; P3 receipt; framework-neutral eval-suite) all landed in `83fe30b`/`f0c950c`. P5's remaining work — the actual hayekAI redelivery, the reorg-branch decision, the cross-repo audit-trail entry — is operator-driven and tracked by `@repo-liaison`, not by this CHANGELOG.

### P2 — Cross-reference warnings eliminated + render validator hardened

- **Zero cross-reference warnings across all example briefs** — `validate_cross_refs` previously emitted warnings for three template patterns where `@slug` references targeted archetypes that are not always co-selected. All three sources fixed:
  - **`orchestrator.template.md` Workflow 10C** — Workflow 10C body steps now carry `*(If @post-production-auditor in team)*` prefixes so the validator correctly skips them when that archetype is absent.
  - **`cohesion-repairer.template.md`** — the prose handoff to `@style-guardian` (line 59) is now prefixed `*(If @style-guardian in team)*`, reflecting the fact that `style-guardian` is a domain-optional archetype.
  - **`module-doc-author.template.md`** — references to the non-existent `@module-doc-expert` slug replaced with `@orchestrator`; the orchestrator is the natural brief commissioner when no dedicated documentation workstream expert is in the team.
- **`render.py` `conditional_re` extended** — added `|Applies only when` pattern as defense-in-depth, so "Applies only when @slug is present" prose guard lines are now recognized and skipped by the cross-ref validator in addition to the existing `*(If @... in team)*` patterns.
- **Example snapshots regenerated** — `examples/{software-project,research-project,data-pipeline}/expected/` snapshots updated to reflect template changes; snapshot tests confirm 0 diffs.

### RCC2 — Render pipeline de-duplicated

- **`_build_final_rendered` helper** — the three inline render pipelines in the `--check`, generate, and `--update` paths of `build_team.py` have been collapsed into a single `_build_final_rendered(manifest, adapter, project_name) -> list[tuple[str, str]]` function. The `--check` path retains its intentional asymmetry: it uses the rendered output for comparison only (no disk emit). The helper runs `render.render_all → adapter.post_process_all → finalize_output_path → runtime handoffs → pipeline graph`.
- **`_make_content_matches` helper** — the two inline `_content_matches` closures in `--check` and `--update` consolidated into a single `_make_content_matches(output_dir, rendered_by_path, security_refresh_paths)` factory returning the predicate.

### Cluster A Phase 2 (increment 1) — framework-neutral eval-suite emission

- **New artifact: `references/eval-suite.json`** — `build_team --update` now emits a framework-neutral behavioral eval suite derived purely from the team manifest (`agentteams/eval_suite.py::build_eval_suite`). Scenarios cover orchestrator routing (knows every workstream expert; expert count == component count), orchestrator-mediated handoff chains (per component `cross_refs`), and the worker-governance triad + "Return to Orchestrator" edge per expert. **Framework-neutral by contract** — contains no Inspect AI / OpenAI Evals DSL tokens (Phase 0 requirement; pinned by `test_eval_suite_is_framework_neutral`). Adapters (Inspect AI, OpenAI Evals) are increments 2–3.
- **Contract parity with the delivery receipt** — schema-validated at write time against `schemas/eval-suite.schema.json`; non-conformance raises `EvalSuiteError` (a `RuntimeError`, never `OSError`) and writes nothing; non-fatal at the call site (heal stands, next `--update` re-emits). Excluded from drift by construction (never in `output_files_map` / `template_hashes` / `file_hashes`; never read by `--check`/`--update`). Top-level discriminator is `artifact_type: eval-suite`.
- **Scope of increment 1** — emission is `--update`-only (mirrors the receipt; avoids generate-path snapshot churn). Generate-path emission + the two framework adapters + Phase 3 behavioral-drift are tracked in `tmp/remediation-plans/master-plan.md` (value rank 1–2).

### P0 — Drift trust + P3 — Update delivery gating

- **P0: `FINGERPRINT_ALGO_VERSION` constant + algo-version field in build-log** — `agentteams/drift.py` now defines a module-level `FINGERPRINT_ALGO_VERSION` constant (currently `1`). `_write_run_log` writes a `fingerprint_algo_version` field alongside `manifest_fingerprint`. A bumped algo version forces a one-shot re-promotion of the unchanged set with reason `"fingerprint algo version bumped"`; pre-version build-logs (missing the field) are treated as legacy — only an actual fingerprint mismatch promotes. The constant is pinned by `test_fingerprint_algo_version_pinned` to force PR review on any future bump.
- **P0: observable baseline self-heal on `--update`** — when `--update` sees `manifest_changed` but content-aware refinement demotes every fingerprint-only promotion (and there is no template/structural/team-membership drift, no `added_files`, no `removed_files`), `build_team.py` now prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).` The heal *write* is implicit via the existing `_write_run_log` call at the end of `--update` — the print just makes the convergence observable. Heal is suppressed under `--dry-run` and never fires when `removed_files` is non-empty (resolution belongs to `--update --prune`).
- **P0: `--check` Option C render-faithful reconciliation (D1)** — `--check` now mirrors what `--update` would write: when `sdreport.manifest_changed AND any(_reason in drift._MANIFEST_PROMOTION_REASONS)`, `--check` renders the full team through the same `render.render_all → adapter post-processing → finalize_output_path` pipeline `--update` uses and runs `refine_manifest_promotion` against the same `_content_matches` closure. Structural-diff output is now printed under the same `has_changes` condition `--update` uses (not just on added/removed). `--check` and `--update --dry-run` now agree on the post-refinement drifted set (pinned by `test_check_parity_with_update_dry_run`).
- **P3: delivery receipt** — `build_team --update` now writes a delivery receipt at `<output_dir>/references/delivery-receipt.json` after the build-log, inside the same `not args.dry_run and result.success` block (the "heal first, attest second" order). The receipt is schema-validated (`schemas/delivery-receipt.schema.json`) and includes `artifact_type: delivery-receipt` (NOT `schema_version`, so build-log readers do not accidentally treat a receipt as a baseline), `manifest_fingerprint`, and `fingerprint_algo_version`. The receipt is excluded from drift artifacts by construction (not in `output_files_map`, `template_hashes`, or `file_hashes`) and is never read by `--check` or `--update`. See `docs_src/delivery-procedure.md` for verification procedures.
- **Docs: delivery procedure guide** — new `docs_src/delivery-procedure.md` documents receipt semantics (attestation, not baseline), CI verification recipes, and the explicit "what the receipt does not prove" contract. Registered under the Guides section of `mkdocs.yml`.

### Infra-audit remediation (W21 adversarial + conflict audit)

- **RA1 — explicit baseline-heal persistence** — the converged `--update` path no longer depends on `security_refresh_paths` keeping the write set non-empty. New `_heal_build_log_baseline()` patches only `manifest_fingerprint` / `fingerprint_algo_version` in place (preserving `file_hashes` / `output_files_map`) when the team is converged but the early "nothing to write" return would otherwise be taken. Heal still never fires on a blocked or `--dry-run` update.
- **RA2 — delivery receipt is now schema-validated at runtime** — `_write_delivery_receipt` validates the payload against `schemas/delivery-receipt.schema.json` before writing and raises `DeliveryReceiptError` (a `RuntimeError`, never an `OSError`) on non-conformance; a non-conforming receipt is *not* written. Both call sites catch `(OSError, DeliveryReceiptError)` non-fatally — the build-log heal stands and the next `--update` re-emits. This makes the previously documentation-only "schema-validated" claim true at runtime (resolves conflict-audit **CC1**).
- **RA5 — narrowed exception** — the `agentteams.__version__` import in `_write_delivery_receipt` now catches only `(ImportError, AttributeError)` instead of bare `Exception`, so an unexpected failure surfaces instead of silently dropping receipt provenance.
- **RRM1 — `.claude/settings.json` gitignored** — the harness-generated, machine-local permission file is now ignored (scoped to the single file; other tracked `.claude/` files are unaffected), ending the per-commit manual exclusion.
- **RSR1 — durable→tmp references eliminated; CI lint guard** — `_write_delivery_receipt` docstring repointed to the versioned `docs_src/delivery-procedure.md` instead of a gitignored `tmp/` planning file. Reference to an off-repo backup of the history-rewrite mirror is documented in the Governance section below. New `scripts/check-durable-tmp-refs.sh` CI lint guard fails when source, schemas, or CHANGELOG contain references to gitignored `tmp/` paths (with allowlist for legitimate discussions of impermanence in CHANGELOG prose like "Mirror backup retained at", "See audit report", etc.). The guard can be run locally as `scripts/check-durable-tmp-refs.sh` or integrated into CI pipelines. Off-repo backup relocation of the pre-history-rewrite mirror is an operator step; see Governance notes below.
- **RSD1 — remediation trackers reconciled; suite-size convention** — `tmp/remediation-plans/master-status.csv` is marked deprecated with a forward pointer to the W21 infra-audit remediation tracker (`tmp/by-week/2026-W21/infra-audit/remediation/master-status.csv`). The new tracker reflects current reality: P0/P3/CC/F4sub all shipped; RCC2/P2/F2-inc1/RA*/RRM1/RSR1 complete in W21. Going forward, each CHANGELOG feature batch entry includes a note like "Suite size after this batch: 710 tests" to support audit reproducibility and per-feature regression attribution (fulfills CN1 traceability requirement).

### Governance: History-rewrite backup durability

The `git filter-repo` mirror created during the history rewrite (pre-rewrite HEAD: `10b8bfc`, post-rewrite HEAD: `b67c514`) is retained for rollback but stored outside this repository. Operator steps to complete RSR1:

1. **Move the mirror to off-repo storage** — Copy `tmp/by-week/2026-W19/rewrite-backups/agentteams.mirror.20260504-160919.git` to your organization's artifact store / cold storage / dedicated backup repository.
2. **Record the durable location** — Document the new location + SHA256 checksum in your organization's runbook or compliance log (not in this repo, which is not suitable for backup metadata).
3. **Mark as complete** — Verify that no tracked file in this repository references `tmp/by-week/2026-W19/rewrite-backups/` (the `scripts/check-durable-tmp-refs.sh` lint guard will pass).

The mirror is not deleted from the local filesystem until the operator confirms availability in the off-repo location.

### Contract notes (read before depending on)

- **D1**: `--check` rendering is gated; outside the fast-path predicate it short-circuits. The structural-diff print scope now matches `--update` (`has_changes`).
- **D2**: P3 enforcement is doc + receipt emission. No CLI flag added; no wrapper command added.
- **D3**: Receipt path is `references/delivery-receipt.json`. Top-level discriminator is `artifact_type: delivery-receipt`. Receipt schema version is `receipt_schema_version: "1.0"` — distinct from build-log `schema_version`.
- **M2**: First `--update` after upgrade rewrites the build-log with the current `fingerprint_algo_version` (the heal). Convergence is asserted by `test_stale_fingerprint_converges_in_two_updates`. Post-RA1 the heal also persists on the converged empty-update path, independent of the security-refresh write set (`test_heal_build_log_baseline_preserves_other_fields`).
- **D4**: A delivery receipt that fails schema validation is non-fatal by contract — `--update` still returns success, the build-log heal stands, and the next run re-emits. Do not treat receipt absence as update failure.

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
