# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### changed

- **`framework-auto-update.yml` converted from auto-merge to a supervised PR.**
  The daily framework-update workflow now opens an `awaiting-human` PR and stops
  — the maintainer reviews and merges manually (matching `advisory-pr` and
  `ai-bad-habits-watch`). Removes `gh pr merge` and the post-merge `ci.yml`
  dispatch (the manual merge triggers CI the normal way), and matches the dedup
  hash across **all** PR states so the open PR is found on later runs and not
  re-created. Supersedes the rc.4/rc.5 auto-merge behavior.
- **Orchestrator pinned to Claude Opus 4.8** (was Claude Sonnet 4.6). Scoped to
  the Tier-1 orchestrator only; all other agent templates remain on Sonnet 4.6.
  Downstream teams adopt it on the next `--update --merge`.
- **`@work-summarizer` daily-capture broadened.** The daily-summary obligation
  fired only when a plan reached all `done`; it now also fires on *executed
  work* — commits, applied migrations/scripts, data mutations, or adjacent-repo
  changes evidenced in operational logs — so a zero-commit primary repo no
  longer exempts a session. Adds a daily-only completeness scan over non-plan
  execution-evidence files (apply-logs, run-results, operation/deletion logs).

### added

- **AI bad-habits catalog + code-hygiene rule CH-25.** New
  `agentteams.ai_bad_habits` — a curated, version-controlled catalog
  (`BH-01..BH-09`) of **code-quality, correctness, and process** habits specific
  to AI agents, each mapped to a corrective pattern. Adds rule **CH-25** (screen
  AI-authored/edited code against the catalog) to `@code-hygiene`, a per-consumer
  `references/ai-bad-habits-watch.reference.md` generated like the security watch
  (template + `build_catalog_placeholders` + `analyze.py` registration), a tracked
  repo-root artifact `references/ai-bad-habits-watch.md`, the
  `scripts/research_ai_bad_habits.py` sync stage, and the supervised-PR
  `.github/workflows/ai-bad-habits-watch.yml` (`awaiting-human`, no auto-merge,
  `workflow_dispatch`-only). Security-class AI habits are deliberately **out of
  scope** — owned by `@security` (see below). Plans + adversarial/conflict audits
  under `references/plans/`.
- **`@security`: AI-authored-code-is-insecure-by-default guidance.**
  `security.template.md` now owns the security-class AI habits with a block
  naming the web-weakness classes AI agents reproduce most (XSS/CWE-79,
  SQLi/CWE-89, CSRF/CWE-352, broken access control/CWE-862), the
  supply-chain/slopsquatting vector, and unsanitized-output-to-sink — closing a
  gap where `@security` previously embedded only the OWASP LLM Top 10.
- **MCP server auto-detection (opt-in, inert).** `schemas/mcp-server.schema.json`
  (allOf hard-gate), `agentteams/mcp_detect.py` (fail-closed
  necessary-condition rubric) wired into `analyze.py`, and
  `agentteams/mcp_emit.py` (gated, self-enforcing emitter — not pipeline-wired by
  design). Adds manifest `mcp_candidates` + project-description `mcp_hints`;
  default emission is unchanged.
- **`--adopt-orphans` flag.** Registers pre-existing agent files the generated
  taxonomy does not produce (bespoke custom agents) into the team roster — the
  orchestrator's handoff list and domain routing — without generating or
  overwriting their files. The opposite of `--prune`. Requires the orchestrator
  to be re-rendered (use with `--overwrite` or `--migrate`).
- **Markdown `project_goal` ingest fallback.** `ingest._load_markdown` derives
  `project_goal` from a ranked overview-style heading (or the first prose
  paragraph) when no explicit `## Project Goal` exists, letting agentteams ingest
  existing `copilot-instructions.md`-style entry files. Hardened for
  setext/fences/lists, length-capped and min-length-guarded.

- **PR management subsystem.** New agents `@pr-manager`, `@pr-notifier`, and
  `@pr-reminder`; Python module `agentteams.pr_management` (recipient-registry
  loader, gh-CLI wrappers, stale-PR scan with dedup, end-of-task
  three-way disposition prompt: `continue-branch` / `push-main` / `open-pr`);
  schema `schemas/pr-recipient-registry.schema.json` with seed
  `references/pr-recipients.json`; daily-cron workflow
  `.github/workflows/pr-reminders.yml` (configurable `REMINDER_INTERVAL_HOURS`,
  `pull-requests:write` only — never merges or pushes); CLI entry-point
  `python -m agentteams.pr_management {prompt,remind}`.
- **Host-feature subselectors (Phase 0).** New `--target-host-features`
  flag accepts comma-separated `<namespace>:<feature>` tokens that gate
  opt-in emission paths. Default emission is unchanged when omitted.
  Public surface: `agentteams.host_features.parse_tokens`, `validate`,
  `is_enabled`. See [API reference](api-reference/host-features.md).
- **Emission baselines (Phase 0).** New `--capture-baseline` /
  `--check-baseline` flags write a deterministic SHA-256 manifest of the
  output tree and diff against a stored one — used by the new
  `tests/baselines/*.json` regression contracts for the two test teams.
  Public surface: `agentteams.baseline.capture`, `write`, `load`, `diff`.
- **Bridge subagent stubs (Phase 2).** With
  `--target-host-features bridge:copilot-vscode-to-claude:subagents`,
  `agentteams --bridge-refresh` (or `--bridge-merge`) emits per-agent
  Claude subagent stubs into `<project>/.claude/agents/` that delegate
  to the canonical copilot-vscode source agent bodies via a `Read`
  directive. Workstream-experts collapse into a single parametric
  `workstream-expert.md` stub. Public surface:
  `agentteams.bridge_subagents.emit_subagent_stubs`,
  `detect_stub_drift`.
- **Bridge Claude hooks (Phase 3).** With
  `bridge:copilot-vscode-to-claude:hooks` selected, the bridge writes
  `.claude/settings.agentteams.example.json` (sample hooks block the
  user merges into their own settings) and `.claude/hook-guard.sh`
  (recursion-depth-bounded notification wrapper; default
  `AGENTTEAMS_HOOK_MAX_DEPTH=2`). The user's own
  `settings.json` / `settings.local.json` is never overwritten.
  Public surface: `agentteams.hooks_emit.build_settings_dict`,
  `emit_hooks_artifacts`.
- **Cache-aware CLAUDE.md emission (Phase 4).** With
  `bridge:copilot-vscode-to-claude:cache-split` selected, the bridge
  replaces its default pointer-only `CLAUDE.md` with a layout that
  inlines `.github/copilot-instructions.md` verbatim followed by a
  `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` marker and a dynamic
  source-SHA-256 / build-timestamp / attribution stanza. Original
  text is preserved as a contiguous substring (verified). Public
  surface: `agentteams.instructions_split.render_cache_split`,
  `verify_equivalence`.
- **/schedule routine emission (Phase 5).** With
  `bridge:copilot-vscode-to-claude:schedule` selected, the bridge
  writes `.claude/schedules.agentteams.json` — recurring routine
  specs (cron + agent slug) for Claude's `/schedule` skill to
  enroll. Default cadences: `work-summarizer` daily, `drift`
  weekly Monday, `post-production-auditor` weekly Friday,
  `advisory` monthly. Routines are only emitted when the matching
  slug exists in the source dir. agentteams does not enroll the
  routines itself. Public surface:
  `agentteams.schedule_emit.build_routines`,
  `emit_schedule_artifact`. `model_routing.agent_tier` extended
  with an `_ALWAYS_CHEAP_SLUGS` set covering per-action lookup
  roles so PreToolUse critic / retrieval-policy / navigator /
  reference-manager / memory-index-query stay on the cheap tier
  regardless of governance-agents membership.
- **CSV plan-steps ↔ TodoWrite projection (Phase 1).** New
  `agentteams.plan_steps_todo` projects the canonical plan-steps
  CSV into TodoWrite-shaped dicts for runtime visibility in
  Claude. Status writeback is append-only and mutates only the
  status column (atomic write). With
  `bridge:copilot-vscode-to-claude:todo-projection` selected, the
  bridge emits `.claude/skills/todo-from-plan.md`. CSV remains the
  audit-bearing plan-of-record; TodoWrite is the projection.
  Public surface: `read_steps`, `project_to_todos`,
  `update_status`, `detect_divergence`, `render_skill`.
- **Consumer-declarable memory-index source dirs.** `brief.json`
  now accepts a `memory_index_extra_dirs` list — project-relative
  directories (recursive `*.md` scan) or glob patterns (literal
  expansion). Safety: absolute paths rejected; traversal rejected
  via `Path.resolve` + `relative_to`; symlink escapes rejected via
  post-glob `os.path.realpath` check. Threaded through
  `analyze.build_manifest` and `build_team._memory_index_sources`.
- **Recall-first clauses in audit / validation / research agents.**
  Six templates gained a fenced `memory_index_consultation` block
  so `@conflict-auditor`, `@conflict-resolution`,
  `@quality-auditor`, `@technical-validator`,
  `@retrieval-integrator`, and `@tool-doc-researcher` call the
  memory-index directly for in-workflow "have we seen / decided /
  audited / researched X before?" lookups instead of round-tripping
  through navigator/orchestrator. Coverage delta on a typical
  37-agent copilot-vscode team: 4 → 9 recall-first agents.
- **Per-strategy memory-index thresholds (v=2).** The four
  audit/validation templates above bumped
  `memory_index_consultation` v=1 → v=2: lexical-first by default,
  vector fallback only on zero hits OR zero query-term overlap
  (single-term false-positive guard), per-strategy thresholds
  (lexical reliable ≥3.0; vector reliable ≥0.30, cap ~0.42).
  Validated against `collector-management`: corpus 69 → 198 docs,
  lexical reliable rate 3/4 → 4/4, vector reliable 0/9 → 3/9 + 1
  candidate.
- **Bridge-refresh Pre-Flight as durable agent invariant.**
  `references/bridge-refresh-safety.md` is now the canonical
  policy. Encoded as constitutional rule 14 in `@orchestrator`,
  invariant-core rule 6 in `@git-operations`, mandatory-review
  trigger in `@security`, protected-files row in `@cleanup`, and
  §D of `references/git-procedures.md`. Records the precaution
  learned from the 2026-05-27 information-loss incident where
  `--bridge-refresh` clobbered user-authored `CLAUDE.md` and
  `.claude/*` content.
- **Code-hygiene rule CH-24 — Exception Handling Is a Last Resort.**
  New invariant extension rule (Defensive Programming, **Critical**) in
  the `@code-hygiene` agent: `try`/`except`/`finally` is reserved for
  genuinely unavoidable external failures (I/O, network, subprocess,
  third-party calls). Expected conditions must instead be encoded in
  dictionaries / lookup tables / explicit guards that **fail hard** on
  the unexpected, so a broken program surfaces immediately rather than
  being masked by broad exception handling — preserving the fast
  iterative debug-and-test cycle. Reinforces CH-23 (Fail Fast on Invalid
  Inputs). Added to `agentteams/templates/universal/code-hygiene.template.md`
  (rule table, consult trigger, delegation row, mandatory-rule bullet),
  the full enforcement section in
  `agentteams/templates/domain/code-hygiene-rules-reference.template.md`
  (preferred control-flow order, prohibited patterns, narrow-catch
  requirements, illustrative `grep` check), and the Unix-philosophy
  mapping (`unix-philosophy-mapping.template.md`, Tier 3 — Transparency +
  Defensive Programming). Example `expected/` snapshots regenerated for
  all four example teams.

### changed

- **Orchestrator model pinned to Claude Opus 4.8.** The tier-1
  Orchestrator template front matter now declares
  `model: ["Claude Opus 4.8 (copilot)"]` (was Claude Sonnet 4.6).
  Scoped to the orchestrator only; all other agent templates remain on
  Sonnet 4.6. Affects newly generated and re-rendered teams; existing
  downstream teams pick it up on the next `--update --merge`. Example
  `expected/orchestrator.agent.md` snapshots regenerated. No CLI/Python-API/
  schema changes.

### fixed

- **`emit`: preserve lost fence bodies as `.lost.<sid>.md`
  sidecars under shrink-warn.** When `--update --merge` replaces a
  fenced region whose existing body contained hand-edits beyond
  the template's body, the full pre-merge body is now written to
  `<backup>/<rel_path>.lost.<sid>.md` and the shrink Notice is
  annotated with the recovery path. Earlier behavior wrote the
  smaller content with only a partial 3-ref hint, leaving the
  operator dependent on whole-file diffing of the backup. New
  surface on `emit.emit_all`: `backup_path: Path | None = None`;
  on `MergeResult`: `lost_fence_bodies: dict[sid, str]`.
- **`emit`: suppress shrink-warn for live-feed-managed fences.**
  The `threat_intelligence` and `threat_data` fences are filled
  from live CISA KEV / NVD / OSV feeds each run; their canonical
  history is the cache JSON, not the embedded snapshot, and CVE
  rotation was triggering shrink-warn on every `--update --merge`.
  Added `_LIVE_DATA_FENCES` allowlist; `_detect_fence_shrink`
  early-returns for those sids. Dry-run shrink notices now
  reference the sidecar-preservation hint (real-run path already
  did).
- **CLI test guard: `test_agent_files_present`.** Pr-agent
  presence test now skips when the gitignored `.github/agents/`
  tree is empty (fresh clone / CI) and only validates structural
  invariants when files are present.

## [1.0.0-rc.6] - 2026-05-27

Advisory-PR pattern. The five in-repo advisory detectors (shrink,
orphan, budget, prefix-cache, operational-JSON) now post their
findings as a labeled PR awaiting operator review, rather than
sitting silently in gitignored logs. Soak clock resets per
pre-release convention; earliest defensible promotion to 1.0.0
final is now on or after 2026-06-03 (one week after rc.6).

No public-API breaks since rc.5.

### added

- **`agentteams.advisory` module.** Aggregates findings from the
  five in-repo advisory detectors into a single PR-ready markdown
  body. Reads the gitignored `tmp/daily-pipeline/` logs the daily
  pipeline already writes; produces empty output when there are
  no findings (caller's signal to skip opening a PR). Public
  surface: `aggregate(today)` and `hash_body(body)`.
- **`scripts/build_advisory_pr.py`.** Wraps the aggregator; writes
  `references/advisories/<today>.md` (tracked) when findings exist;
  prints `findings=true|false`, `hash=<12hex>`, `path=<rel>` to
  `GITHUB_OUTPUT` for the workflow's downstream steps.
- **`.github/workflows/advisory-pr.yml`.** Runs daily at 07:47 UTC
  (after bridge-maintenance and framework-auto-update). When
  findings exist, commits the advisory file on a transient
  `advisory/<hash>` branch and opens a PR with labels `advisory` +
  `awaiting-human`. Does **not** auto-merge. Operator merges to
  commit the audit record, closes to dismiss, or comments with
  guidance for the next pass.
- **`references/advisories/` directory** (tracked, initially
  empty). Each merged advisory PR adds one dated file.
- **Labels `advisory` and `awaiting-human`** created on the remote
  via `gh label create` out-of-band.

### changed

- **Daily-pipeline integration.** The 5 advisory detectors continue
  to write their gitignored tmp/ logs unchanged; the new workflow consumes
  those logs as the aggregation source.
- **Behaviour on no-drift days.** No findings → no advisory PR.
  Stable findings across days → dedup by content hash; same
  findings as an open PR produce no second PR.

### maintenance

- **Self-team orphans cleaned up** (one-time, local). Six
  `.agent.md` files left over from earlier team configurations
  (`best-practices-expert`, `docs-research-expert`,
  `implementation-guidance-expert`, `module-doc-expert`,
  `pipeline-health-expert`, `post-production-auditor`) deleted
  from the local `.github/agents/` tree (gitignored — no commit).
  The orphan detector is now silent for the agentteams self-team.

### tests added

- `tests/test_advisory.py` (6 cases) — empty/with-findings paths
  for each detector, dedup-hash stability and sensitivity.
- `tests/test_advisory_pr_workflow.py` (7 cases) — workflow shape:
  cron + dispatch, minimal permissions, **no `gh pr merge`** (the
  key contract distinguishing this from framework-auto-update),
  advisory labels applied, step summary emitted, distinct branch
  prefix from the auto-update workflow.

## [1.0.0-rc.5] - 2026-05-27

Post-merge safety net restored. Drift-detector inventory audited.
Soak clock resets per pre-release convention; earliest defensible
promotion to 1.0.0 final is now on or after 2026-06-03 (one week
after rc.5).

No public-API breaks since rc.4.

### fixed

- **`framework-auto-update.yml` now dispatches `ci.yml` after the
  auto-merge.** Production test of rc.4 (workflow run 26518916462,
  merge commit `25afe9f`) revealed that the rc.4 CHANGELOG claim
  "the merge commit on main fires the normal CI run, which is the
  post-merge safety net" was wrong: GitHub's GITHUB_TOKEN
  infinite-loop safeguard suppresses workflow runs caused by
  GITHUB_TOKEN events, including the merge event itself.
  Empirical: `gh api .../commits/25afe9f/check-runs` returned
  empty. rc.5 closes the gap by calling
  `gh workflow run ci.yml --ref main` after the merge.
  `workflow_dispatch` is **exempt** from the GITHUB_TOKEN filter,
  so the dispatched CI run actually fires.

### added

- **`workflow_dispatch:` trigger added to `ci.yml`.** Required by
  the rc.5 dispatch call above. Side benefit: operators can now
  manually fire CI against any commit on main.
- **CI run URL in the auto-update step summary.** The post-execution
  report now lists the dispatched-CI run URL alongside the PR URL,
  hash, and merge SHA — single-screen audit per cycle.

### audited (no code change)

- **Drift-detector inventory.** 12 detectors classified across the
  codebase: 6 auto-implementing (framework upstream drift, security
  threat-intel, bridge drift, template content, tool-scope at CI
  time, watchdog auto-issue) and 6 correctly advisory because
  mechanical auto-fix would be unsafe (shrink, orphan, dual-
  descriptor, budget, prefix-cache, operational-JSON). Full
  inventory in
  `references/plans/rc5-drift-detector-inventory-2026-05-27.plan.md`.
  No gaps requiring closure.

## [1.0.0-rc.4] - 2026-05-27

Auto-merge release. The daily pipeline now implements revisions
automatically rather than waiting for a human merge action. Soak
clock resets per pre-release convention; earliest defensible
promotion to 1.0.0 final is now on or after 2026-06-03 (one week
after rc.4).

No public-API breaks since rc.3.

### changed

- **`framework-auto-update.yml` auto-merges its own PR.** The
  workflow now runs `gh pr merge --merge --delete-branch`
  immediately after `gh pr create`. PRs created by `GITHUB_TOKEN`
  do not trigger CI (GitHub's infinite-loop safeguard), so there
  is no CI gate to await; the merge commit on `main` fires the
  normal CI run, which is the post-merge safety net. Branch
  protection on `main` is unchanged — the workflow merges through
  the same PR surface that a human would. Reversibility: standard
  `git revert` of the merge commit.
- **`automerge:false` label dropped** from auto-PRs. The label
  conflicted with the new behaviour; `framework-update` remains
  for discovery filtering.

### added

- **Post-execution report via `GITHUB_STEP_SUMMARY`.** Each
  `framework-auto-update` run emits a step summary listing the PR
  URL, proposal hash, merge commit SHA, and the merged diff
  (first 200 lines). Recorded in the GitHub Actions run UI so
  every cycle has an auditable "what landed today" surface.

## [1.0.0-rc.3] - 2026-05-27

Agent-efficiency release. Soak clock resets per pre-release
convention; earliest defensible promotion to 1.0.0 final is now
on or after 2026-06-03 (one week after rc.3).

No public-API breaks since rc.2. New efficiency lints are
advisory; the one template change (terse-mode directive) is
additive and propagates to consumers via `--update --merge`.

### added

- **Per-agent token-budget + prompt-cache prefix lint
  (`--check-budget`).** New `agentteams.budget` module audits live
  `.agent.md` files for two efficiency dimensions. Budget warns
  when a non-orchestrator agent exceeds 300 lines, fails at 600
  lines (orchestrator-class fail threshold: 1000 lines). Prefix-
  cache flags ISO-date patterns within the first 60 lines
  outside HTML comments — volatile content in the prefix defeats
  Anthropic prompt-cache hits on every refresh. CLI exits 1 on
  fail-class findings, 0 on warn-class only.
- **Daily-pipeline integration of the budget audit.**
  `scripts/run_daily_bridge_maintenance.sh` invokes the audit as
  a non-critical advisory step. Remediation routes to
  `@agent-refactor` per the constitutional gate.
- **Tone-and-style fence in the copilot-instructions template.**
  Declares: read-only auditor and governance roles default to
  ≤200-word responses; producing roles are explicitly exempt so
  they aren't silenced when emitting deliverables. Reduces
  consumer-harness token consumption on the common case of
  audit-and-route turns.

### fixed

- **`conflict-auditor` template was over-scoped.** Its role
  description says "Detects logical conflicts" — pure audit, with
  routing to `@conflict-resolution` for the actual edits. The
  template previously declared `['read', 'edit', 'search',
  'execute']`. Trimmed to `['read', 'search']` to match the
  contract. The new `tests/test_agent_tool_scopes.py` regression
  keeps it honest across `security`, `adversarial`,
  `code-hygiene`, and `conflict-auditor`.

### tests added

- `tests/test_budget.py` (9 cases).
- `tests/test_agent_tool_scopes.py` (6 cases).
- `tests/test_terse_mode_directive.py` (3 cases).

## [1.0.0-rc.2] - 2026-05-27

Second release candidate. Soak continues; no public-API breaks
since rc.1. Bugfixes against the supervised auto-update loop, plus
small quality-of-life features and a test-extras refactor.

### fixed

- **Auto-PR dedup hash no longer includes today's date.**
  `framework_research.propose_module_patch` now emits a top-level
  `dedup_hash` field (proposal schema bumped to 1.2) computed only
  over upstream tokens and adapter constants. The
  `framework-auto-update.yml` workflow's hash step reads this
  field instead of hashing the rendered new_text. Effect: on
  no-drift days, the proposal hash matches the prior day's PR,
  the existing dedup check finds the open PR, and no duplicate
  is created. Symptom that triggered the fix: rc.1 opened a fresh
  PR each scheduled run even when observed tokens were
  byte-identical.
- **Blank-line accumulation in observation splices.**
  `_splice_observation_block` now collapses any run of three or
  more newlines to a single blank line after substitution.
  Previously each daily refresh added one extra blank line above
  the heading.

### added

- **`--shrink-policy=halt` pre-flight in dry-run.** `emit.emit_all`
  with `dry_run=True, shrink_policy="halt"` populates
  `EmitResult.shrink_blocked` with paths that a real run would
  refuse, without modifying any file. Lets operators preview a
  halt-mode posture before adopting it.
- **Auto-PR labels.** The `framework-auto-update.yml` workflow now
  applies `framework-update` and `automerge:false` labels at PR
  creation. Improves the discovery surface and signals to future
  reviewers that the PR must not be auto-merged. Labels created
  on the remote with `gh label create`.
- **Operational-JSON allow-list audit in the daily digest.**
  `scripts/daily_pipeline_digest.py` walks the gitignored
  `.github/agents/references/*.json` tree and flags any
  non-allow-listed file whose lines exceed a 5% density of
  absolute paths or high-entropy hex tokens. Catches future
  generated files escaping `scan._OPERATIONAL_JSON_NAMES` before
  they re-block the daily security scan.
- **`[project.optional-dependencies] test` extras group.**
  pyproject declares `test = ["pytest>=8", "pyyaml>=6"]`; CI
  workflows now install via `pip install -e ".[test]"`. Runtime
  dependency list unchanged (jsonschema only); the wheel stays
  small.

### infrastructure

- **Initial scheduled auto-update fires hardened during soak.**
  Three issues found and fixed on 2026-05-26:
    - `pytest` was missing from `framework-auto-update.yml`'s
      install step.
    - `actions/permissions/workflow.can_approve_pull_request_reviews`
      defaulted to `false`; enabled via `gh api`.
    - A stale transient branch from the failed first dispatch was
      cleaned up.
  Result: subsequent auto-update PRs opened successfully under
  the supervised pattern.

## [1.0.0-rc.1] - 2026-05-25

First release candidate for the 1.0 line. Functionally complete; in soak
for at least one week before promotion to 1.0.0 final.

### highlights

- **Daily pipeline gains framework-research and a supervised module-core
  update path** (full surface enumerated under "rolled-up changes" below).
- **Stability contract published** ([STABILITY.md](STABILITY.md)) enumerating
  the public surface covered by SemVer and the surfaces explicitly excluded.
- **Security policy published** ([SECURITY.md](SECURITY.md)) with disclosure
  process and threat model.
- **Classifier moved to `Development Status :: 5 - Production/Stable`.**
- **`__version__` is now single-sourced** from installed package metadata
  via `importlib.metadata`; no more drift between `pyproject.toml` and
  `build_team.py`.
- **`build-team` console-script alias is now soft-deprecated** — it still
  works through the 1.x series but emits a stderr deprecation notice on
  every invocation. It will be removed at 2.0. Switch to `agentteams`.
- **Packaging audit** caught and fixed a leak where untracked-on-disk
  scratch directories (the gitignored `tmp/`, `references/plans/`, etc.)
  were being pulled into the wheel and sdist by setuptools' default file
  discovery. Now constrained via `[tool.setuptools.packages.find]`
  includes and a `MANIFEST.in` with explicit `prune` directives.
  Wheel: 337K; sdist: 446K.
- **Branch protection set on `main`** — required PR (0 approvals,
  solo-maintainer policy: PR is the gate, owner self-merges), force-push
  blocked, deletion blocked, enforce_admins=false (owner break-glass
  available).

### rolled-up changes since 0.1.0

#### Daily-pipeline framework-research and module-core update path

**New CLI flags and entry points**

- `--shrink-policy {warn,halt,allow}` (default `warn`): controls
  `emit.emit_all` behaviour when a fenced-region merge would drop
  concrete references. `warn` (back-compat) logs and writes; `halt`
  refuses the write and lists the blocked file under
  `EmitResult.shrink_blocked`; `allow` writes silently. The
  self-team daily script (`scripts/run_daily_security_maintenance.sh`)
  adopts `halt`; consumer-repo invocations stay on the default `warn`.
- `scripts/research_claude_code_docs.py --propose | --apply` — thin
  CLI wrapper around `agentteams.framework_research`. Propose writes
  `tmp/daily-pipeline/framework-research/proposal.json` (gitignored).
  Apply runs `tests/test_frameworks.py` + `tests/test_framework_research.py`
  and reverts on failure. CI-refusal lifted only when
  `AGENTTEAMS_ALLOW_CI_APPLY=1` is set (auto-PR workflow only).
- `scripts/daily_pipeline_digest.py` — delta-only quality digest
  aggregating framework-research, shrink-events, dual-descriptor-events,
  orphan-events, and bridge-maintenance summary into a single
  `tmp/daily-pipeline/digest/<date>.md` (gitignored).

**New public module: `agentteams.framework_research`**

Mirrors the contract of `agentteams.security_refs.build_security_placeholders`.

- `FRAMEWORK_REGISTRY` — three frameworks: `claude`, `copilot_vscode`,
  `copilot_cli`. Each entry records its doc URL, expert-reference
  path, and allow-listed token set.
- `refresh_snapshot(repo_root, offline=False) -> dict` — fetches the
  multi-framework snapshot; writes `latest.json` (schema 1.1) with
  Claude-level top-level keys preserved for back-compat plus a
  `frameworks` dict.
- `build_framework_placeholders(output_dir, offline=True) -> dict[str, str]` —
  returns `FRAMEWORK_RESEARCH_*` placeholders for the
  `framework-watch.reference.md` template.
- `propose_module_patch(repo_root) -> dict` — produces a v1
  observation-stanza proposal for the Claude and Copilot expert
  references. Never proposes constant mutations.
- `apply_module_patch(proposal, repo_root) -> dict` — allow-list
  restricted (`ALLOWED_EXPERT_REFS`); refuses to run when `CI=true`
  unless `AGENTTEAMS_ALLOW_CI_APPLY=1` is also set.

**New generated reference (every consumer team gets it)**

- `<output>/references/framework-watch.reference.md` — single
  `framework_data` fence populated from the snapshot; one row per
  framework with observed tokens.

**Quality-signal artefacts (delta-only, gitignored)**

All paths below live under the gitignored daily-pipeline tree
(`tmp/daily-pipeline/`) — Operator-local state, never durable:

- `framework-research/latest.json` plus dated research reports
  (gitignored).
- `shrink-events/<date>.md` (gitignored) — fenced-region shrink
  notices, with `backup_dir:` linking to the
  `.agentteams-backups/<ts>/` containing the pre-shrink content.
- `dual-descriptor-events/<date>.md` (gitignored) — emitted when a
  consumer repo has both `brief.json` and a sibling
  `.github/agents/_build-description.json` diverging on
  `{project_name, primary_output_dir, reference_db_path, deliverables}`.
- `orphan-events/<date>.md` (gitignored) — agent files on disk not
  in the current team's manifest.
- `digest/<date>.md` (gitignored) — aggregator.

**`emit.emit_all` changes**

- New kwarg `shrink_policy: str = "warn"` (see above).
- New field `EmitResult.shrink_blocked: list[str]` — paths skipped
  due to halt-mode.

**`agentteams.scan.scan_directory` changes**

- New kwarg `expected_agent_names: set[str] | None = None`. When
  provided, `.agent.md` files outside this set are treated as
  orphans and skipped (the orphan advisory surfaces them
  separately).
- Walk now skips `.agentteams-backups/` subtrees (point-in-time
  snapshots, not live content).
- Placeholder matches inside inline-code spans (`` `…` ``) are
  skipped (documentation prose mentioning placeholder names).
- `_SECRET_CONTEXT_RE` now word-bounded so prose like "tokenized"
  doesn't trip on adjacent identifier-shaped strings.
- Operational-metadata JSON allow-list `_OPERATIONAL_JSON_NAMES`
  (`build-log.json`, `delivery-receipt.json`, `memory-index.json`,
  `eval-suite.json`, `doc-hashes.json`): suppresses PII path,
  entropy, and placeholder detection in these files;
  pattern-based credentials (`sk_live_*`, `xoxb-*`, etc.) still
  apply.
- `_SAFE_TOKENS` adds `PLACEHOLDER` and `UPPER_SNAKE_CASE` as
  meta-documentation tokens.

**`agentteams.analyze` changes**

- New `_default_reference_db_path` / `_default_style_reference_path`
  helpers infer `docs/` / `docs_src/` when the descriptor declares
  a `doc_site_config_file` and the directory exists on disk.
  Eliminates the persistent `{REFERENCE_DB_PATH}` / `{STYLE_REFERENCE_PATH}`
  manual placeholders for projects with mkdocs (and similar).

**`build_team.py` changes**

- `_check_dual_descriptor` advisory fires after `--description` is
  resolved; never reads the sibling, never modifies either file.
  Self-update is exempt (the sibling IS the descriptor there).
- `_persist_shrink_events` / `_persist_orphan_events` helpers
  append to the daily logs above. Wired into both emit code paths
  (`--update` branch and post-emit main path).

**New / updated workflows**

- `.github/workflows/framework-auto-update.yml` — supervised
  auto-PR workflow. Runs daily (cron `23 7 * * *`), refreshes
  snapshot, runs `--propose`, dedups by proposal hash against
  existing open PRs, applies on transient branch
  `auto/framework-update-<hash>`, opens PR via `gh pr create`.
  Permissions: `contents: write`, `pull-requests: write`.
- `.github/workflows/bridge-maintenance.yml` — artifact upload
  now includes the gitignored `tmp/daily-pipeline/` directory so
  the watchdog can inspect the framework-research snapshot.
- `.github/workflows/bridge-watchdog.yml` — restructured into
  three steps: locate latest run, `gh run download` its artifact,
  evaluate both workflow-age AND snapshot-age. Detects the case
  where the workflow succeeded but the non-critical research stage
  silently failed.

**Repo policy**

- Branch protection set on `main` (2026-05-25):
  required PR (0 approvals — solo-maintainer policy: PR is the
  gate, owner self-merges), force-push blocked, deletion blocked,
  enforce_admins=false (owner break-glass available).

**Tracked bootstrap aid**

- `references/_self-build-description.template.json` — operator
  copies to `.github/agents/_build-description.json` (gitignored).
- `references/SELF-BUILD-DESCRIPTOR.md` — bootstrap procedure.

**Tests added (44 new tests; 924 total)**

- `tests/test_framework_research.py` (8 cases)
- `tests/test_dual_descriptor.py` (3 cases)
- `tests/test_daily_pipeline_digest.py` (2 cases)
- `tests/test_orphan_events.py` (4 cases)
- `tests/test_analyze_defaults.py` (5 cases)
- `tests/test_shrink_policy.py` (3 cases)
- `tests/test_auto_update_workflow.py` (6 cases)
- `tests/test_scan.py` extensions (5 new cases: backup skip,
  backtick spans, operational-JSON suppression, word-bounded
  secret context, real-token still fires)

#### Earlier changes since 0.1.0

The full set of behavior changes accumulated under `[Unreleased]`
between 2026-04-15 and 2026-05-23 is preserved verbatim below.

### fix(ci): memory-index relevance test now skips on incomplete corpus (2026-05-23)

`tests/test_memory_index_relevance.py` was failing on every CI matrix leg with
8/10 top-1 accuracy (against a 9/10 threshold). Root cause: the EVAL_PAIRS were
calibrated against the developer corpus that *includes* `references/plans/` —
which is gitignored (51 of `.gitignore`). A fresh clone carries only the 1
committed plan file out of ~50+ locally; without that background, BM25
tie-breaking shifts two queries to a near-duplicate doc and the test fires a
spurious failure. The test's own docstring already describes it as
"skipped when the corpus is absent" — the `skipif` just under-checked. Now
also requires `references/plans/` to have >=10 .md files. Locally passes
3/3, on CI skips 3/3. Reproduced the CI failure locally by renaming
`references/plans/` aside and re-running.

### API docs: phantom-source fix + public emit surface (2026-05-22)

Audit of `docs_src/api-reference/*.md` against the agentteams code surface:

- **Phantom-source attribution fixed.** `security-refs.md` carried a `### ROUTING_SCHEMA_VERSION` section attributed to `agentteams/security_refs.py`, but the constant lives in `agentteams/model_routing.py` (where it is already correctly documented). Removed the misattributed duplicate.
- **Documented public emit dry-run surface that was missing.** Added `### DryRunEntry` (the dataclass populating `DryRunReport.entries`) and `### print_dry_run_report(result, manifest, *, fmt='text')` to `emit.md`. Both have been public-facing for some time but had no dedicated doc section.
- Spot-check: `emit_all` signature in docs matches code; `drift.FINGERPRINT_ALGO_VERSION` is correctly documented (the initial scan flagged it as a phantom but the constant exists as a typed assignment).

### Security hardening: --migrate gate exemption is in-process only (2026-05-22)

- **Audit finding (HIGH):** the `--from-migrate` flag introduced with the prior
  `--migrate` hardening was a parseable CLI flag (`argparse.SUPPRESS` only hides
  from `--help`, not from argv). A user who knew the name could pass
  `agentteams ... --overwrite --from-migrate --yes` to bypass the
  destructive-action security gate **without going through `--migrate`'s
  snapshot-tag safety** — a regression I shipped one commit earlier.
- **Fix:** removed `--from-migrate` from the CLI parser entirely. The gate
  exemption is now reachable only via a module-level flag
  (`_MIGRATE_GATE_EXEMPTION_ACTIVE`) set by `_run_migrate` around its `main()`
  re-invocation, scoped by `try/finally`. A direct CLI invocation cannot reach
  the exemption path.
- Regression test:
  `tests/test_migrate.py::test_from_migrate_is_not_a_cli_flag` asserts the
  flag is absent from `_build_parser()`. Full suite green at **899 passed**.

### Orphan-agent advisory in --update (2026-05-22)

- **`--update` now reports agent files on disk that the current team no longer
  emits.** `--prune` only removes agents dropped since the last build
  (`StructuralDiffReport.removed_files`, a build-log diff); files orphaned by
  *older* team-config changes — which the build log no longer records —
  previously accumulated invisibly. Surfaced by self-updating the agentteams
  team (6 such orphans found in `.github/agents/`). Report-only advisory; no
  deletion.
- Test: `tests/test_integration.py::test_update_reports_orphan_agent_files`.

### --migrate hardening + researchteam test update (2026-05-22)

Surfaced by using the `researchteam` repo (12 fenced / 17 legacy agent files) as
a live test of `--update --merge` and `--migrate`:

- **Legacy-skip warning** (`emit.py`) now recommends `--migrate` alongside
  `--add-fence-markers` and `--overwrite`.
- **`--migrate` no longer hard-errors on a stale `pre-fencing-snapshot` tag** —
  with `--yes` it moves the tag to current HEAD.
- **`--migrate`'s internal `--overwrite` is exempt from the security-decision
  gate** (internal `--from-migrate` marker) — `--migrate` carries its own safety
  via the snapshot tag.
- **`--revert-migration` is no longer gated by the security check.** It is a
  recovery operation restoring a deliberate checkpoint; gating the rollback path
  left a failed `--migrate` unrecoverable via the CLI.
- Test: `tests/test_migrate.py::test_migrate_moves_stale_tag_with_yes`.
- **Finding (not yet a fix):** `--migrate` is `--overwrite`-based, so it
  regenerates agent bodies from templates and discards post-generation
  enrichment not captured in `_build-description.json` — verified destructive
  against `researchteam` (a curated literature list in `primary-producer`), then
  reverted. The safe update path for a mixed legacy/fenced repo is plain
  `--update --merge`: it updates fence-ready files and skips legacy files
  **intact**. A content-preserving migration remains a design follow-up.
- Full suite green at **897 passed**.

### Fence-based Invariant Core boundary + structural lint (2026-05-22)

- **AUTHORING-GUIDE §3.2–§3.3 revised** so the Invariant Core is defined as the agent file's FENCED region — a machine-checkable, merge-enforced boundary — rather than a heading convention. Implements Recommendation R1 / Finding F1. The adversarial audit of the original plan replaced a ~32-template heading-demotion pass with this zero-churn definition, which fixes F1 better: the fence boundary cannot silently drift.
- **New `tests/test_doc_structure.py`** — structural lint over freshly rendered agents: every persona carries a balanced fenced Invariant Core region and a USER-EDITABLE `## Project-Specific Notes` section outside all fences; reference files carry neither. Implements Recommendations R3 + R5 (merged per audit — the fence-based boundary makes the fence, not the manifest, the authoritative structural contract).
- Completes the four-plan agent-document-structure metaplan (2026-05-22). Full suite green at **896 passed**.

### USER-EDITABLE Project-Specific Notes section for agent files (2026-05-22)

- **Every emitted agent persona now carries a `## Project-Specific Notes` USER-EDITABLE section** outside all `AGENTTEAMS` fences (`emit._ensure_project_notes_section`) — the first-class, merge-safe home for per-project rules and overrides. Implements Recommendation R2 of the 2026-05-22 structure assessment and resolves Finding F2 (domain-archetype agents previously had zero user-editable region). Reference and instruction files are excluded.
- **Migration follows path b:** the section is appended to merged output as well as fresh renders, so existing fleet files gain it on `--update --merge`. Pure append — project-authored orphan fences and hand edits outside the templated structure are preserved verbatim.
- **`build_team.py` `_make_content_matches`** updated to mirror the new emit output so drift refinement still demotes content-identical files.
- Tests: 3 new `tests/test_emit.py` cases; example snapshots regenerated (109 files); full suite green at **887 passed**.

### Canonical heading taxonomy for agent documents (2026-05-22)

- **`AUTHORING-GUIDE.md` §3 rewritten as a Canonical Heading Taxonomy** (was "Required Sections by Tier"). Defines the document spine — `# Title` (H1), `## Invariant Core` (H2, FENCED), `## Project-Specific Notes` (H2, USER-EDITABLE) — plus canonical per-tier H3 subsections and the Invariant Core boundary rule (it is a bounded container, not a label). §2 cross-references the new boundary rule. Implements Recommendation R4 of the 2026-05-22 agent-document-structure assessment.
- **Standards impact: major.** Per the guide's own versioning standard, requiring a new document structure is a major agent-documentation standards change; templates must be migrated to conform (tracked as plan P2 — Invariant Core + per-project editable regions). No template or emitted file changed by this entry yet — taxonomy definition only.

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
