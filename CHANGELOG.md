# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Retrieval-integrator template reference extraction (2026-05-21)

- **`retrieval-integrator.template.md` — inline contract snapshot extracted to references.** The agent template's volatile `Contract Snapshot` block (retrieval mode, trigger contract version, query/maintenance entrypoints, trigger sources, source of truth, staleness SLO) is replaced with two `#file:` pointers to the already-generated `references/retrieval-integration.reference.md` and `references/retrieval-trigger-contract.reference.md`. Both reference files are emitted under the same archetype gate as the agent, so the pointers always resolve. Invariant Core, Validation Procedure, and Output Format remain inline; the now-redundant `CH14:ALLOW_INLINE_DATA` marker was removed with the extracted block. Behavior-preserving: a regenerated retrieval team showed no information loss and no unresolved placeholder tokens.
- **Tests: 3 new template-content regression tests in `tests/test_render.py`** — assert the retrieval reference linkage, the absence of the extracted inline contract placeholders, and the retention of the inline invariant/procedure sections.
- **Scoped extraction only.** The companion tool-specialist refactor was audited and **rejected** — specialist-tier tools never emit a `references/{slug}-reference.md` (the specialist and reference tiers are mutually exclusive in `analyze.py`), so the proposed `#file:` pointer would have been a fabricated reference. The module-doc shared-reference extraction stays **deferred** pending unmet phase-2 entry criteria. Full suite green at **887 passed**.

### Two-day implementation/debug hardening addendum (2026-05-19 to 2026-05-20)

- **Retrieval integration contract hardening shipped across pipeline + templates**:
  - Added retrieval integration schema contracts in `schemas/project-description.schema.json` and `schemas/team-manifest.schema.json`.
  - Added conservative repository inference for retrieval mode/entrypoints/trigger sources in `agentteams/ingest.py`.
  - Added normalization + manifest propagation + retrieval archetype auto-selection + retrieval reference planning in `agentteams/analyze.py`.
  - Added new retrieval artifacts in templates: `agentteams/templates/domain/retrieval-integrator.template.md`, `agentteams/templates/universal/retrieval-integration.reference.template.md`, and `agentteams/templates/universal/retrieval-trigger-contract.reference.template.md`.
  - Added regression coverage in `tests/test_ingest.py` and `tests/test_analyze.py`.

- **Copilot adapter reliability hardening shipped after snapshot-debug cycle**:
  - Hardened YAML team filtering in `agentteams/frameworks/copilot_vscode.py` for mixed `agents:` flow-list/block-list variants and flexible handoff formatting.
  - Added no-op formatting preservation when filtered membership is unchanged to avoid cosmetic snapshot drift in generated orchestrator output.
  - Refined optional applicability suppression in `agentteams/render.py` to reduce false unresolved cross-reference warnings.
  - Added/updated tests in `tests/test_frameworks.py` and `tests/test_render.py`; full suite verification completed at **877 passed**.

### Dual-mode manual placeholder policy (usability default + strict self mode)

- Added CLI flags `--strict-manual-placeholders` and `--no-strict-manual-placeholders` to control whether optional unresolved governance placeholders remain as `{MANUAL:*}` tokens or are replaced with explicit `N/A` defaults.
- Introduced manifest policy application in `build_team.py` so default module runs favor usability:
  - `{MANUAL:REFERENCE_DB_PATH}` -> `N/A - no citation database configured for this project`
  - `{MANUAL:STYLE_REFERENCE_PATH}` -> `N/A - no formal style guide defined for this project` (or `style_reference` value when provided)
- Strict mode now defaults to enabled in `--self` runs; non-self runs default to usability mode.
- Updated docs: enrichment pipeline guide, CLI reference, and enrich API/module reference.
- Added regression tests covering strict/non-strict policy transforms and strict-mode resolution precedence.

### Agent-prompt wiring for the W21 substrate (init + update parity)

Closes the integration gap surfaced by the 2026-05-20 evaluation: substrate (eval-suite, memory-index, delivery-receipt, backup-manifest, shrink-Notice, model-routing, typed handoffs) was fully shipped, but only 3 of 9 behavioral surfaces were wired into agent prompts. This batch edits the **templates** so every newly-initialized team AND every `--update`d team automatically gains the wiring (both pipelines render from the same templates).

- **`orchestrator.template.md` — new Workflow 10D (Behavioral Verification)** between 10B/10C and Workflow 11. Reads `references/eval-suite.json`, instructs the operator to translate via an Inspect AI or OpenAI Evals adapter, invokes `agentteams.behavioral_drift.detect_behavioral_drift` on any recent `agent_session_trajectory` packet, and escalates HARD findings to `@conflict-auditor`. Absent-artifact fallback: skip Workflow 10D with a one-line note and proceed to Workflow 11. Workflow 11's existing non-recursion guard now lists 10D alongside 10B/10C.
- **`conflict-auditor.template.md` — typed-handoff audit (PROSE-first per F-RM1) + behavioral-spec cross-check.** Two new conflict codes `PAYLOAD_MISMATCH` / `PAYLOAD_UNTYPED` formalize the audit of adjacent plan-step payload schemas in plain language; `agentteams.handoff_payloads.audit_handoff_chain` is referenced afterwards for engineering integration. New "Behavioral spec cross-check" section instructs the auditor to verify routing / handoff / governance scenarios against the emitted team. `references/eval-suite.json` added to the Reference Layer audit scope. Both checks skip silently when the artifact is absent.
- **`adversarial.template.md` — memory-index consultation in Temporal/Causal classes.** Step 2 (Classify Each Presupposition) now instructs the auditor to query `references/memory-index.json` before adjudicating T/C presuppositions; cite the pointed document only when the snippet is *clearly responsive*. Absent/stale/empty index falls back to filesystem search + `git log` — never blocks. The index is explicitly framed as a history layer, not authoritative.
- **`work-summarizer.template.md` (domain) — memory-index-first for weekly/monthly summaries.** Queries the index before scanning the filesystem; skipped for daily summaries (too short-horizon to benefit). Falls back to direct file reads on absence/stale-index. Conditional on the work-summarizer being in the team (4 of 5 examples currently — the `@navigator` nested protocol remains the unconditional F8 path).
- **`agent-updater.template.md` — four additions covering the W21 `--update` improvements:** (1) `--dry-run --json` piping for programmatic review; (2) `--cost-routing` opt-in documentation (default OFF, framework-neutral tier contract emission); (3) post-update delivery-receipt fingerprint-parity check against the just-written build-log (P3 invariant); (4) backup-manifest rollback recipe with per-file SHA-256 verification (W21 Plan 2) plus shrink-Notice stderr review step (W21 Plan 3).
- **Tests: `tests/test_agent_feature_wiring.py`** (7) — one test per wired directive, asserting the new text is present in a freshly-rendered data-pipeline team. Prose-first ordering for the typed-handoff rule is pinned (F-RM1 correction).
- **Deliberate snapshot refresh: 20 expected/ files** (5 examples × 4 always-emitted agents) — the only `expected/` diff in this batch. `work-summarizer` refreshed in the 4 examples that emit it; `learn-python-for-stats-and-econ` has no `expected/` directory and is excluded from snapshot testing.
- **Suite size after this batch: 843 tests** (was 836; +7 wiring regression tests). Full suite, man-page parity (no CLI surface change), `verify-env.py` preflight, and RSR1 tmp-guard all green.

### W21 `--update` improvements metaplan (4 plans)

Four module-improvement plans surfaced by the 2026-05-19 `learn-python-update-data-loss-audit`, executed in metaplan order. All additive; full suite **836 passed** (was 810; +26 across the four plans).

#### Plan 1 — `--update --dry-run` structured preview

- New `--dry-run` semantics for the `--update` and generate paths: previews every per-file action (`WRITE` / `OVERWRITE` / `MERGE` / `MERGE-OVERWRITE-FENCED` / `UNCHANGED` / `SKIP`) and per-fence-region action (replaced / added / orphaned) **without writing anything** (no files, no backups).
- New `--json` flag pairs with `--dry-run` to emit the plan as a single JSON document on stdout (pipes to `jq`).
- `agentteams/emit.py` now exposes `DryRunEntry` / `DryRunReport` dataclasses; `EmitResult.dry_run_report` is populated on dry runs and `result.notices` is a unified channel both runs use. The reporter is an explicit *extension point* (Plan 3 hooks into `notices`/`DryRunReport.notices`).
- 5 new tests (`tests/test_update_dry_run.py`): API shape, text mode, JSON mode, dry-run+overwrite, and dry-run/real-run consistency.

#### Plan 2 — Backup manifest sidecar

- Every `.agentteams-backups/<timestamp>/` directory now contains a `_manifest.json` sidecar documenting per-file `source_path` / `backup_path` / `source_size_bytes` / `source_sha256` plus a header (`agentteams_version`, `framework`, `description_path`, `output_root`, `reason`, `timestamp_utc`, `total_files`, `total_bytes`). Schema: `schemas/backup-manifest.schema.json`.
- `emit.backup_output_dir(... reason=, framework=, description_path=)` is the single backup site; both `build_team.py` callers pass an explicit `reason` (`pre-update` / `overwrite-mode` / `pre-overwrite` / `merge-overwrite-fenced`).
- `restore_backup` skips `_manifest.json` (metadata, not restored content).
- 3 new tests (`tests/test_update_backup_manifest.py`): manifest on `--update`, manifest on `--overwrite`, SHA-256 integrity against on-disk backup files.

#### Plan 3 — Fenced-section shrink Notice

- During a merge, when a regenerated fence body is materially shorter or less specific than the existing on-disk body, a `Notice:` is queued on `MergeResult.shrink_notices` → aggregated into `EmitResult.notices` → printed once to stderr at end of run.
- Detection rules (any one triggers): (a) new body length < 50% of existing; (b) ≥ 3 fewer markdown list items; (c) concrete file paths or backtick-quoted identifiers present in the existing body but absent from the new body.
- Markdown-only by construction (fence merges only apply to `.md`). Dry-run surfaces the same Notices into the structured report (Plan 1 D-4).
- 8 new tests (`tests/test_update_shrink_notice.py`) covering each rule, no-fire thresholds, content-grew, and `_merge_fenced_content` end-to-end.

#### Plan 4 — Legacy-file fence-marker injection helper

- New module `agentteams/fence_inject.py` + `inject_fence_markers(path, mode='sidecar'|'in-place', confirm_in_place=False)` that retrofits canonical `AGENTTEAMS:BEGIN/END` markers around a legacy file's existing body so it becomes eligible for future merge-mode `--update`.
- **Sidecar (default):** writes `<name>.fenced.md` alongside the source — non-destructive. **`--in-place`:** requires `--yes` (and is documented to require `@security` clearance); creates a timestamped `.agentteams-backups/` backup before mutating.
- Idempotent on already-fenced files (no-op, no sidecar written). Retrofit fence-id rule: base `legacy_body`, suffix `legacy_body_<n>` on collision — documented in `agentteams/templates/PLACEHOLDER-CONVENTIONS.md`.
- New CLI flags `--add-fence-markers PATH` and `--in-place`; runs before any description-loading so works on standalone legacy files. YAML front matter (if present) stays above the BEGIN marker.
- 10 new tests (`tests/test_fence_inject.py`): sidecar default, YAML-front-matter ordering, in-place + backup, in-place without confirm raises, idempotency, fence-id collision suffix, four CLI surface tests.

#### Coordinated cross-plan invariants

- Plan 1's reporter is an extension point; Plan 3's shrink Notices flow through it without forking the dry-run logic (the metaplan's cross-plan risk #1).
- Plan 3 detection is markdown-only by construction (cross-plan risk #2).
- Plan 2's manifest is written at the single `emit.backup_output_dir` site, which is the only backup-creation site in the codebase (cross-plan risk #3 verified).
- Plan 4 `--in-place` mode requires explicit `--yes`; CLI gates it (cross-plan risk #4).
- Man page (`agentteams.1`) deliberately regenerated to absorb the new `--json`, `--add-fence-markers PATH`, and `--in-place` flags.

### `--update` defaults to merge; `--overwrite` required for destructive re-render

- **Breaking CLI change: `--update` now defaults to merge mode** — `--update` alone now preserves all user-authored content outside fence markers (equivalent to the former `--update --merge`). Full destructive re-render now requires `--update --overwrite`, which invokes the security gate. Existing scripts using `--update --merge` continue to work unchanged. Scripts using `--update` alone that relied on full overwrite must be updated to `--update --overwrite` and must have a valid `references/security-decisions.log.csv` clearance for action `overwrite`.
- **Security gate bypass for default update** — the security gate for destructive overwrites is no longer invoked for plain `--update`. It fires only when `--overwrite` is explicitly passed, removing friction from routine update workflows.
- **mtime hygiene in overwrite path** — `emit.emit_all` now skips the write when an existing file has byte-identical content, even in overwrite mode. Files with unchanged content are reported as `unchanged` rather than `written`, preventing spurious mtime bumps and downstream re-triggers.
- **`--help` text updated** — the `--update` flag description now describes merge as the default and directs users to `--overwrite` for full regeneration.
- **Orchestrator template updated** — the `project_rules` section manifest note and the USER-EDITABLE callout now document `--update` as the merge-default command. The former `--update --merge` phrasing has been replaced throughout the template, example outputs, CLI reference, migration guide, and update lifecycle guide.
- **Tests**: 3 new regression tests added (`test_overwrite_unchanged_content_not_written`, `test_update_alone_bypasses_security_gate`, `test_update_overwrite_triggers_security_gate`); 1 existing integration test updated.

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

### Cluster A Phase 2 (increments 2+3) — eval-framework adapters

- **New package `agentteams/eval_adapters/`** — code-generator adapters that translate the framework-neutral eval-suite into a specific eval framework. Adapter modules import **no** eval framework: the coupling lives only in the emitted artifact, so agentteams takes no Inspect AI / OpenAI Evals dependency and `eval_suite.py` stays neutral (honors the generator-owned-artifact scope test).
- **Increment 2 — Inspect AI adapter** (`eval_adapters/inspect_ai.py`) — `render_inspect_ai_module(suite)` emits runnable Inspect AI task source: one `@task` per scenario, an embedded `structural_scorer` interpreting all four neutral predicate kinds (`frontmatter-list-contains-all`, `agent-count`, `handoff-chain`, `frontmatter-and-body`) against `AGENTTEAMS_TEAM_DIR`. Pure + a `write_*` wrapper.
- **Increment 3 — OpenAI Evals adapter** (`eval_adapters/openai_evals.py`) — `build_openai_evals_definition(suite)` emits an OpenAI-Evals-shaped JSON definition (registry entry + `id` + `metrics` + per-scenario `samples` with predicates preserved); the structural grader is referenced by class path (`STRUCTURAL_GRADER_CLASS`) since Evals registries cannot inline code — the OpenAI-Evals analogue of the Inspect adapter's embedded scorer.
- **Isolation pinned by tests** — `tests/test_eval_adapter_inspect_ai.py` (7) + `tests/test_eval_adapter_openai_evals.py` (7): adapters import without the target framework loaded, output is syntactically valid (Inspect: `ast.parse`; Evals: `json.loads`), one task/sample per scenario, all four predicate kinds handled, and the neutral suite is neither mutated nor decoupled.
- **Scope** — increments 2+3 are standalone modules; no `build_team.py` wiring (emission of adapter outputs into a team is a later increment). `eval_suite.py` and `build_team.py` are untouched. Next per master-plan: F2 increment 1b (generate-path emission, sequence after RCC2 — already shipped) then F5 behavioral drift.
- **Suite size after this batch: 737 tests** (was 723; +14 adapter tests). Full suite + man-page parity + preflight + RSR1 tmp-guard all green.

### Cluster A Phase 2 (increment 1b) — generate-path eval-suite emission

- **`build_team` now emits `references/eval-suite.json` on first generation too**, not only on `--update` (increment 1 was `--update`-only to avoid snapshot churn before RCC2). Wired in Step 9 alongside `_write_run_log`, gated by the same `not args.dry_run and result.success`, non-fatal on `EvalSuiteError`/`OSError` (next run re-emits). Safe to touch the generate path now that RCC2 unified the render pipeline. The artifact stays `.json` and drift-excluded, so the `.md`-only snapshot suite is unaffected (verified). Pinned by `test_generate_emits_eval_suite_increment_1b`.

### Cluster A Phase 3 — behavioral drift detection

- **New module `agentteams/behavioral_drift.py`** (deliberately *not* `drift.py` — distinct from template/structural/manifest drift). `detect_behavioral_drift(trajectory, eval_suite)` compares a recorded run trajectory (Phase 1 `agent_session_trajectory` replay substrate) against the Phase 2 framework-neutral eval-suite's `handoff-chain` scenarios, and **reuses Cluster C `audit_handoff_chain`** for typed-payload continuity along the actual edges walked. This closes Cluster A: drift is now detected at the *behavioral* level, not just the file level.
- **Findings vocabulary** (reuses the Cluster C `Finding` dataclass): `BEHAVIOR_CHAIN_DIVERGENCE` (actual chain matches no expected chain), `BEHAVIOR_MISSING_RETURN` (correct chain but no orchestrator mediation — peer-to-peer drift), `BEHAVIOR_BROKEN_CHAIN` (non-contiguous edges), `BEHAVIOR_NO_TRAJECTORY` (suite expects a chain, none ran), plus pass-through `PAYLOAD_MISMATCH`/`PAYLOAD_UNTYPED`. A conforming run yields `[]`.
- **Gate met:** `tests/test_behavioral_drift.py` (8) — conforming run passes clean; injected divergence (skipped node, missing mediation, broken contiguity, payload break) is flagged; empty-suite/empty-trajectory is clean.
- **Suite size after this batch: 746 tests** (was 737; +8 behavioral-drift, +1 generate-path eval-suite). Full suite + man-page parity + preflight + RSR1 tmp-guard all green.

### F6 — Cost / model-routing protocol (OFF by default)

- **New CLI flag `--cost-routing`** (default `False`, `store_true`). When **absent** (the default), behavior is byte-identical to the prior release at the *generated agent-file* level — pinned by `test_default_off_emits_no_routing_artifact_and_is_byte_identical` (OFF and ON runs produce identical `orchestrator.agent.md` / `navigator.agent.md` / expert files). The flag itself changes `--help` and the committed `agentteams.1` man page — a deliberately regenerated CLI-surface artifact (audit Correction 1).
- **New artifact `references/model-routing.json`** (emitted only when the flag is set, at all three sites: generate Step 9, `--update` heal-converged, `--update` normal). Same RA2 contract as the eval-suite/delivery-receipt: schema-validated against `schemas/model-routing.schema.json` at write time; non-conformance raises `ModelRoutingError` (a `RuntimeError`, never `OSError`) and writes nothing; non-fatal at the call site; **excluded from drift** by construction (`.json`; never in `output_files_map`/`template_hashes`/`file_hashes`).
- **Framework-neutral by contract.** The contract assigns each agent a tier *role* (`primary` / `cheap` / `fallback`) — never a concrete model string. Tier rule derived purely from the manifest: `manifest["governance_agents"]` ⇒ `cheap`; everything else (orchestrator, workstream experts, primary-producer, domain/support agents) ⇒ `primary` (conservative — an unknown agent is never downgraded). Resolution to concrete models is the runtime/adapter's job, mirroring the eval-suite neutrality.
- **Explicit non-goal:** the rendered `model:` line in agent files is **not** modified. Rewriting it would churn snapshots and couple the neutral output to framework model strings. F6 ships the routing contract; the runtime consults it.

### F8 — Retrieval-backed memory index (additive, nested navigator protocol)

- **New module `agentteams/memory_index.py`** — pure, dependency-free **lexical BM25** index over durable text sources (`workSummaries/**/*.md`, `CHANGELOG.md`, `README.md` at the project root). Public API: `build_memory_index(sources, *, project_name="", framework="")` and `query_index(index, query, *, k=5)`. Deterministic; missing/unreadable sources are silently skipped; empty source list ⇒ a valid empty index. Vector/embedding retrieval is an explicit later tier (heavy deps + nondeterminism — out of this increment).
- **New artifact `references/memory-index.json`** (emitted unconditionally at all three sites: generate Step 9, `--update` heal-converged, `--update` normal). Same RA2 contract: schema-validated against `schemas/memory-index.schema.json`; non-conformance raises `MemoryIndexError`; non-fatal; drift-excluded by construction (`.json`).
- **Additive — never a replacement.** The existing work-summary documents and `references/work-summary-spec.reference.md` are **untouched**. The index is built *from* them and stored alongside. Pinned by `test_generate_emits_drift_excluded_memory_index_additive` (asserts the source docs' content survives unchanged).
- **Nested navigator protocol (`navigator.template.md`)** — new Invariant Rule 2 directs the navigator to: (a) query the lexical index first, (b) cite the snippet if it answers, (c) **open the specific referenced document** for full detail if the snippet is insufficient, (d) only then fall back to filesystem search. **Absent/stale-index fallback** (audit Correction 3): "If `references/memory-index.json` is absent, empty, or its snippets do not answer, proceed directly to (c)/(d) — never block on the index." The work-summary docs remain the source of truth; the index is a fast-lookup layer that may be stale between `--update` runs.
- **Deliberate snapshot refresh** — `examples/{software-project,research-project,data-pipeline}/expected/navigator.agent.md` regenerated to absorb the new Invariant Rule (audit Correction 2; the only `expected/` diff in this batch).
- **Generate-time emptiness is honest** (audit Correction 4) — a freshly generated downstream team has no work summaries yet; the index is empty/minimal at that point and accrues value on later `--update`s of long-lived teams. Documented in the module docstring.
- **Suite size after this batch: 762 tests** (was 746; +7 model-routing, +9 memory-index). Full suite, man-page parity (with the deliberately regenerated `agentteams.1`), `verify-env.py` preflight, and RSR1 tmp-guard all green.

### F8 — Trigger placement audit + refinements

Audit of F8's trigger points (emission sites, source-collection, navigator consultation, fallback) surfaced three Medium findings, all fixed in this same batch:

- **F-1 (Rule 1 ↔ Rule 2 overlap).** Both navigator rules pattern-matched on "*where is X?*", producing inconsistent agent behavior. Rule 1 reworded to scope it explicitly to **structural / current-file** queries; Rule 2 reworded to scope it to **historical / decision / prior-work** queries. The two trigger surfaces are now orthogonal.
- **F-2 (source-collection ignored operator's project root).** `_memory_index_sources` used `output_dir.parent.parent` to infer the project root, silently producing an empty index when `--output` is non-standard but the description supplies `existing_project_path`. `agentteams/analyze.py::build_manifest` now propagates `existing_project_path` into the manifest; `_memory_index_sources(manifest, output_dir)` prefers it. Pinned by `test_existing_project_path_overrides_output_dir_inference`.
- **F-3 (no low-confidence guard).** Rule 2 wording now requires the snippet to be *clearly responsive* before citing — a weak top-BM25 result is treated as "snippets do not answer" and falls through to opening the document / filesystem search.
- **F-7 (test coverage gap).** Only the generate-path emission was integration-tested; the two `--update` sites were wired identically but untested. New `test_update_path_reemits_memory_index` exercises the `--update` path and asserts the index picks up newly-added work summaries.

Accepted (documented, no code change): **F-4** snippet truncation (mitigated by the open-the-document fallback); **F-5** index staleness between runs (mitigated by the same fallback; wording already calls it out); **F-6** `convert`/`interop`/`bridge` paths emit none of the RA2 artifacts — F8 is *symmetric* with its peers (delivery-receipt, eval-suite, model-routing). Recorded as a known gap, not a defect.

The trigger-audit changes propagate to `navigator.agent.md` in all three examples — a second deliberate snapshot refresh of `examples/{software-project,research-project,data-pipeline}/expected/navigator.agent.md`.

- **Suite size after the trigger audit: 764 tests** (was 762; +2 trigger-audit regression tests). Full suite, man-page parity, preflight, RSR1 tmp-guard all green.

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
