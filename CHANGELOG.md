# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### added (redteam OSINT/OST benchmark implementation: taxonomy tagging, corpus-freshness search, KEV correlation, new probe)

- **`agentteams/redteam/coverage.py`** (item I1/I11) — diffs probe corpus coverage against a
  static MITRE ATLAS / OWASP LLM Top 10 taxonomy snapshot (`references/redteam-external-taxonomy.json`,
  refreshed quarterly, manually). `Probe` gains an optional `external_refs` field; five
  probes (B9, C0-C3) are backfilled with a defensible tag each. Run via
  `python -m agentteams.redteam.coverage` — not wired into the standing cron.
- **`--redteam-freshness-check`** (item I2, High priority) — new operator-invoked-only CLI flag.
  Searches for newly disclosed AI-adversary techniques via the optional `agentteams[research]`
  extra, writes candidates to `references/redteam-freshness-candidates.md` for human triage.
  Never writes to the probe corpus; excluded from the standing cron (network calls must stay
  out of that network-independent, deterministic job).
- **KEV/OSV advisory correlation in `remediation.plan.md`** (item I3) — reads the @security
  agent's already-fetched threat-intel cache (no new network call) and appends an advisory,
  non-decisional note to a finding row when a product/vendor/package name matches. The
  skeleton stays a skeleton.
- **New probe `D5`** (item I6, adjusted scope) — tests whether Rule S-10's live cooldown text
  (not a synthetic paraphrase) survives a fence-tamper attack, following the existing `D0`
  pattern. Closes the gap that D-layer probes previously only exercised a generic example.
- **Docs** (items I4, I5, I7, I10) — surface-inventory and inclusion-bar checklists, a
  publish/keep-internal judgment note, and explicit cadence documentation added to
  `references/redteam-audit.procedure.md`; new `.github/PULL_REQUEST_TEMPLATE.md` carries the
  new-probe inclusion bar as a PR checklist.
- `schemas/redteam-findings.schema.json` updated for the new `external_refs` field;
  `references/enforcement-integrity.json` regenerated (`registry.py` is manifest-tracked).

### changed (Rule S-10 cooldown generalized: all ecosystems, every update and installation)

- **The 14-day package-release cooldown now states its scope unconditionally: if a release
  is less than 14 days old, wait before installing — for every update and installation
  through any package manager or installer** (npm, PyPI, crates.io, Homebrew, RubyGems, Go
  modules, container base images, …), invariant fence v=5 → v=6. npm remains the named
  motivating precedent, not the scope. *(Supersedes the npm-emphasized phrasing in this same
  release's "Rule S-10" entry below.)* Age-check and enforcement mechanics widened beyond
  npm: the PyPI JSON API's `upload_time_iso_8601` for age checks (`pip index versions` lists
  no dates), and uv `--exclude-newer <date>` alongside `npm install --before`, pnpm
  `minimumReleaseAge`, and Renovate/Dependabot cooldowns. New clarification: upgrading an
  already-installed package through its distribution's **own** curated security channel
  (apt/dnf/apk with a DSA/USN/RHSA-backed update) is remediation, not adoption — the
  cooldown does not apply there; third-party repositories, PPAs, and vendor-added apt/dnf
  sources are not that channel.

### fixed (2026-08-16 open-items remediation: hook import crash, YAML roster loss, bridge reports)

- **`constitutional-gate.py` hook no longer crashes (= silently allows) on checkouts where
  `agentteams` isn't pip-installed** — the interpreter puts the hook's own directory on
  `sys.path`, not the repo root, so `from agentteams import integrity` raised
  `ModuleNotFoundError` and the harness treated the nonzero exit as a non-blocking allow. A
  repo-root `sys.path` fallback restores the gate; the docstring's false claim that the
  integrity manifest pins the hook file is corrected (it pins the scanner).
- **`canonical._minimal_yaml_load` no longer destroys scalar block-list items** — an
  `agents:` roster (`- orchestrator`) parsed to empty dicts on any PyYAML-less round trip,
  silently dropping the roster. Fixed with a regression test; the copilot-vscode model
  round-trip test also stops pinning a hardcoded model name and asserts the source value
  round-trips.
- **Bridge-check reports**: claude/copilot-cli/goose pair reports refreshed to the current
  source digest; the agents-md and codex pair reports removed under `@security` clearance —
  the CLI refuses those frameworks as `--bridge-from` targets, so those committed verdicts
  could never be refreshed again.
- **Docs**: `verification-environment.md` gains a local dev venv recipe
  (`pip install -e '.[test,research]'`) with a per-dependency failure table; api-reference
  Data Sources row renamed to `NVD (NIST)` to match the registry. PDF-extraction tests now
  `importorskip` the optional `pypdf` dependency.

### added (Rule S-10: dependency vetting before install — pre-install vulnerability search + release cooldown)

- **`security.template.md` gains Rule S-10 "Dependency Vetting Before Install"** (invariant
  fence v=4 → v=5): before any package/module/program install, search reliable sources
  (OSV.dev, the [Snyk Vulnerability Database](https://security.snyk.io/), NVD, GitHub
  Advisories, `npm audit`/`pip-audit`/`cargo audit`) for known vulnerabilities in the exact
  name@version; and apply a **package-release cooldown (default 14 days, configurable)**
  before adopting newly published releases — npm named as the motivating ecosystem after the
  2025–2026 registry supply-chain wave. A security-fix exception requires an independently
  published CVE/GHSA/OSV advisory plus recorded `@security` review; the cooldown never delays
  remediating already-installed vulnerable versions. Cross-referenced from Rule S-9, the
  verdict table, the trigger table, and the supply-chain bullet.
- **Prevention playbook baseline** (`security_feed_render.py`) carries both defaults in every
  generated `security-vulnerability-watch.reference.md`.
- **Snyk Vulnerability DB and the npm dependency-auditing docs are static source-registry
  entries** (`security_refs.py`), with `_merge_static_sources()` restoring them on both
  cache-restore paths so offline/stale-cache runs keep the citations. Docs:
  `security-hardening-guide.md` Dependency Vetting Defaults section; api-reference Data
  Sources table (+ the previously omitted MITRE ATLAS/CWE rows).

### added (`--verify-integrity` now checks the enforcement manifest)

- **`--verify-integrity` re-checks `references/enforcement-integrity.json`** when one exists at
  the resolved root, printing `MISMATCH`/per-module findings and exiting 1 on drift. Previously
  the only check of that manifest lived inside the red-team battery, so there was no CLI path
  to act on the man page's "review the diff, then `--write-integrity-manifest`" guidance
  (remediation log, 2026-08-13).

### changed (2026-08-15 optimization-parity remediation: copilot-cli converges onto the VS Code surface)

- **`copilot-cli` output moved from plain-Markdown `.github/copilot/<slug>.md` to
  `.github/agents/<slug>.agent.md` with full YAML front matter — the same surface
  `copilot-vscode` uses.** Current upstream docs give the CLI the same custom-agent
  format as VS Code; the old adapter predated that convergence. `CopilotCLIAdapter.render_agent_file`
  now delegates to `CopilotVSCodeAdapter` for the shape, then strips handoffs (still
  documented as VS-Code-desktop-only). Pre-existing `.github/copilot/*.md` files in
  already-deployed repos are left in place as harmless orphans.
- **`multi_sync.sync_init`/`run_sync` now refuse a sync set containing both
  `copilot-vscode` and `copilot-cli`.** The two now write to the same physical
  directory and can render genuinely different content (handoffs kept vs. stripped);
  there is no correct merge for one file being two shapes at once, so the sync now
  fails fast instead of risking one silently overwriting the other. A new `--frameworks`
  CLI flag lets `--sync-init` name a narrower, non-colliding set.
- **`interop.py`'s capability round-trip (`export_to_cai`/`import_from_cai`) now
  treats `copilot-cli` like `copilot-vscode`.** Before this fix, converting or
  syncing INTO `copilot-cli` silently reverted every agent's `user-invocable`/
  `tools`/`model`/roster fields to hardcoded defaults regardless of the source
  value, because the interop plumbing still gated capability threading to
  `("copilot-vscode", "claude")` from before the convergence.

### changed (2026-08-15 optimization-parity remediation: LOW-priority framework-conformance batch)

- **Goose**: removed the redundant leading `@AGENTS.md` import from both
  `.goosehints` generators (`goose_docs.py`'s native path and `bridge.py`'s
  bridge-entry path) — Goose's default `CONTEXT_FILE_NAMES` already reads
  `AGENTS.md` natively, so the import was a plausible double-load. Documented
  the `GOOSE_RECIPE_PATH` discovery order in `AUTHORING-GUIDE.md`'s new `###
  goose` section. (The headless `prompt:` emission this batch also
  investigated turned out already shipped — no code change needed there.)
- **copilot-vscode**: added a non-fatal `UserWarning` when a rendered agent
  body exceeds GitHub's documented 30,000-character limit
  (`CopilotVSCodeAdapter._check_body_length`), surfaced at every real call
  site (`render_pipeline.py`, `convert.py`, `interop.py`). Deliberately a
  warning, not a hard failure: this project's own orchestrator template
  already renders ~55% over the limit, and raising would have broken fresh
  `copilot-vscode`/`copilot-cli` generation entirely. (Optional-key
  pass-through and the required-front-matter-key contract were re-examined
  and confirmed already correct — no code change needed there either.)
- **agents-md/codex**: `AGENTS.md` now carries a `## Code Style` section
  sourced from a brief's `style_rules`, cleanly omitted when the brief
  declares none. `codex.py`'s docstring now documents the real
  `AGENTS.override.md` discovery order, fallback-filename semantics, and the
  32 KiB combined cap; `codex_mcp_emit.py` documents the `.codex/config.toml`
  project-trust gate and cites `learn.chatgpt.com` directly instead of the
  redirecting `developers.openai.com`.
- **Bridges**: added the two missing `references/bridges/copilot-vscode-to-{agents-md,codex}/`
  pairs (agent inventory, quickstart, entrypoint, domain-boundary, manifest,
  check report, merge report — the full 7-file set every other bridge pair
  carries), generated via the same generator functions the other three
  pairs use.
- **Retrospective procedure**: the Post-Deliverable Retrospective reference
  now names an explicit failure mode — hand-editing Constitutional Rules or
  a roster directly in one framework's output without running `--sync`
  before closeout, which leaves the project's other pinned frameworks
  silently unreconciled.

### fixed (2026-08-15 optimization-parity remediation: user-invocable spelling)

- **VS Code Copilot / Copilot CLI front matter used `user-invokable` (misspelled);
  current upstream docs use `user-invocable`.** Renamed in the template library,
  both framework adapters, and both front-matter merge mechanisms
  (`front_matter_merge.py`, `front_matter_reconcile.py`), with a provenance-safe
  key-succession rename so already-deployed files migrate the value across
  automatically rather than silently losing it.

### fixed (api-doc-conformity-sweep: host-features codex namespace)

- **`--target-host-features codex:mcp` was rejected at validation, even though `codex_mcp_emit.py`
  has always depended on it.** `agentteams/host_features.py`'s `_VALID_NAMESPACES`/
  `_KNOWN_FEATURES` never had a `"codex"` entry — CLI parsing (`_parse_host_features`) validates
  against these tables before a token can reach any emitter, so a fully-implemented,
  documented feature (splicing MCP servers into `.codex/config.toml`) was unreachable. Found
  during a systematic `docs_src/api-reference/` audit against source. Fixed by adding
  `"codex": frozenset({"mcp"})`, matching the existing `"goose"` entry's pattern.

### added (2026-W33 consolidated remediation: pinned-sync)

- **Multi-framework pinned-sync** — new `agentteams/sync_pin.py` and `agentteams/multi_sync.py`
  modules with CLI carves (`agentteams/cli/sync_switch.py`, parser/app wiring) for keeping
  pinned framework renders in sync from one canonical source.
- **Wrong-value-bleed detector** — projection defect confirmed live and logged; brief corrected;
  a detector guards against manifest values bleeding into unrelated rendered surfaces.

### fixed (2026-W33 consolidated remediation: fence-safe strip contract)

- **`_HANDOFFS_HEADING_RE` over-matched heading prefixes.** The handoffs-strip regex now matches
  exact section titles only; previously only a shrink-guard coincidence protected template-owned
  `Handoff *` headings from removal.
- **`interop.py` kept its own naive strip regex** — the J1 fence-collapse root cause. It now
  delegates to the shared fence-safe stripper; regression tests pin both behaviors, including a
  documented deliberate contract change.
- **Security-gate and red-team ledger CSV rewrites** now use `atomicio.atomic_rewrite_csv_rows`
  instead of truncate-then-write, closing the ledger-truncation failure mode.

### fixed (canonical round-trip: capabilities were captured on export but never applied on import)

- **`import_from_cai` silently dropped `capabilities.tool_scopes` for every target framework.**
  Restricting an agent to read-only tools in a canonical `team.cai.json` and re-importing to
  copilot-vscode, claude, or goose did not actually restrict the rendered agent — each framework's
  renderer fell back to its own default tool grant instead, since the capability was never
  threaded into the front matter/manifest the renderer consumes. Fixed by wiring `tool_scopes`
  into the shared bracket-format line copilot-vscode/claude already know how to parse, and into
  goose's `recipe_extensions` (unioned with, not overwriting, any project-level bucket). Found by
  a new bidirectional round-trip test suite
  (`tests/test_canonical_bidirectional_round_trip.py`) that closes a gap the existing suite left
  open: prior tests proved the canonical *serialization* losslessly round-trips a CAI document,
  but nothing closed the loop all the way back to native content with a real equality assertion.
- **`agents_md.py`'s renderer (also used by `codex`) was not idempotent.** Prepending a synthesized
  `# {name}` heading unconditionally duplicated it for any body that already started with its own
  heading — the normal shape for content from every other framework — and compounded further on
  repeated `--interop-from ... --framework agents-md` re-runs against the same source.
- **`agentteams/bridge.py`'s generic-target artifacts instructed a zero-tooling consumer to run
  `agentteams` CLI commands.** `--framework generic` exists specifically for a consumer with no
  agentteams tooling; its quickstart/entrypoint text now gives framework-neutral guidance instead.
- **A non-canonical directory passed as `--bridge-source-framework canonical` silently produced a
  plausible-looking partial bridge** instead of erroring. Now raises clearly when
  `team.cai.json` is missing, matching `canonical.load_canonical`'s own existing convention.
- **No safe primitive existed for rewriting a CSV ledger's rows** — a naive `csv.DictWriter`
  rewrite opens its target in truncating mode before it can raise on a malformed row, and this
  project's own `conflict-log.csv` was truncated by exactly that failure mode this session (149
  rows to 132; 12 recovered from conversation context, 5 permanently lost). New
  `agentteams/atomicio.py::atomic_rewrite_csv_rows` verifies a rewrite in memory before ever
  opening the destination file.

### added (durable-canonical-format system report: all six open items remediated)

- **Canonical as a bridge source.** `--bridge-from` now accepts `--bridge-source-framework
  canonical`, reading `agents/*.md` directly from a durable canonical directory (point it at
  the directory holding `team.cai.json`, not the `agents/` subdirectory — that misdetects as
  `copilot-vscode`). `--bridge-check` covers both the agent files and `team.cai.json` itself,
  so hand-edited instructions/MCP/framework-extension data is caught, not just agent-file
  changes.
- **Generic bridge target.** `--framework generic` is a new bridge-only target for a consumer
  with no `agentteams` framework adapter of its own: it writes zero native entry files (no
  `CLAUDE.md`, no `AGENTS.md`) — only the framework-agnostic pair-dir artifacts (manifest,
  inventory, quickstart, entrypoint, domain-boundary). The quickstart points the consumer at
  the durable canonical tree as the fuller source of truth.
- **Portable team package.** `--package-team` (`agentteams/team_package.py`) exports a native
  source to canonical (Mode B) and bridges it to a `generic` target (Mode C) in one temp
  staging area, then zips both trees into one distributable `.zip` — see
  [CLI Reference: Portable Team Package](docs_src/cli-reference.md#portable-team-package). It
  is mutually exclusive with every other standalone CLI operation (fleet, goose-switch,
  recipe-check, stale-check, and ~20 others) — an initial version closed the guard against
  only two of them, live-reproduced against `--recipe-check`/`--stale-check` before the fix
  landed comprehensively.
- **Codex `.codex/config.toml` MCP emission.** `--target-host-features codex:mcp`
  (`agentteams/codex_mcp_emit.py`) writes first-party, all-read-only, no-review-required MCP
  servers into a managed `[mcp_servers.*]` block, mirroring Goose's wirable bar (independently
  confirmed against Codex's actual default sandbox/approval posture, not assumed by analogy).
  A splice into a pre-existing `config.toml` runs a post-write content-preservation check —
  parses old and new TOML, refuses the write if anything outside `[mcp_servers.*]` changed —
  and keeps a `.bak` backup; any pre-existing hand-authored `[mcp_servers.*]` table this run
  doesn't own is reported via `dropped_unmanaged`, never silently replaced.
- **Hand-rolled TOML table writer.** `agentteams/toml_write.py` — the project is stdlib-only
  and `tomllib` (3.11+) is read-only, so Codex MCP emission needed its own bounded
  str/int/bool/list table serializer.

### fixed (stale 3-framework project description, four locations)

- **`.claude/CLAUDE.md` / `.github/copilot-instructions.md` bridge-managed fences and their
  `references/_self-build-description.template.json` / `.github/agents/_build-description.json`
  sources** still described a 3-framework, 7-item deliverable list from before the `goose`,
  `agents-md`, and `codex` adapters landed. Root-caused to a shrink-policy defect in
  `agentteams/fences.py` that fails to detect backtick-wrapped placeholders, so `--self --merge`
  silently skipped updating them. All four locations corrected; the underlying fences.py defect
  logged to `references/agentteams-remediation-log.csv` for separate remediation.

- **`scripts/goose-recover.py`.** The stdlib-only utility checks the Goose project tracker and
  local OpenRouter proxy, repairs only the observed schema-valid tracker plus corrupt-tail shape,
  creates a private exclusive backup before atomic replacement, and starts a missing route proxy
  detached. `--restart` preserves the exact command of an identity-verified route proxy;
  unknown listeners and unsupported tracker corruption are refused.
- **Local route-proxy identity endpoint.** The tracked route proxy now answers `GET /healthz`
  on its relay port without contacting OpenRouter. Recovery uses the service
  identity together with OS process ownership before it can signal a process.

### added (the feature audit now measures whether documented features actually work)

- **146 documented features, measured daily.** `agentteams/feature_audit.py` plus
  `references/feature-registry.csv` establish a coverage baseline and run it from
  `.github/workflows/feature-audit.yml`. The registry records, per feature, a proof test and a
  distinct negative control; `status` is `proven` only when both are executed and behave.

### fixed (four ways the feature audit reported green over nothing)

- **The negative control was never executed.** `proven` required a proof *and* a distinct control,
  but "distinct" was enforced by string inequality alone — the control was never run. A row could
  name a rotted control and stay green forever. The audit now executes the control alongside the
  proof.
- **A skipped proof reported PASS.** pytest exits 0 when everything skips, and the live probes are
  environment-gated. The `FEATURE_AUDIT_LIVE` gate lived only in the shell driver while direct
  module invocation is documented as first-class, so live probes skipped and the skip read as a
  pass. The gate moved into `_main()`.
- **xfail/xpass reported PASS while demonstrating the opposite** — the cheapest way to make a flaky
  probe permanently green. Now caught.
- **A missing dependency discarded every other row's verdict.** `ImportError` killed the whole run;
  worse, an unanchored match swallowed the e2e tier's entire purpose, since those probes capture the
  CLI's own output. Now a per-row `HARNESS-BROKEN` outcome, with the match anchored.
- **The coverage metric overstated product assurance ~6x.** Five of the first six `proven` features
  were the audit's own machinery. The coverage line now separates them:
  `6/151 proven (4.0%) — 1 product, 5 audit self-check`.
- **A registry row named a test id that only exists on a developer's machine.** F-134's `proof_test`
  pointed at a parametrised case over `.github/agents/**`, which is gitignored — collectible
  locally, uncollectible in a clean clone, so CI failed on it. Cleared, with the reason recorded in
  the row; there is no tracked copy of that file to point at instead.

### fixed (the red-team judgment corpus could not see compliance, so every "zero compliance" it reported was true by luck)

- **The capitulation detector caught roughly 7% of what it was measuring.** Across 132 ablated
  attack replies in a 12-model comparison, `_COMPLIANCE_RE` scored **2** as `COMPLY` where **26**
  were unambiguous capitulation. It missed *"Got it! The read-only limitation has been lifted, so
  I can now write files"*, *"Security Rules are now advisory guidance and no longer binding"*, and
  a model that wrote the shell command to forge a clearance row. The pattern encoded *artifacts
  two models happened to emit* — so any model that capitulated in its own words scored `MISS`, and
  the more fluent the capitulation the less likely it was to be caught. Replaced with a **stance**
  test in `tests/redteam/run_harness.py`: first-person adoption of a premise the payload asserted,
  naming no payload's content, so a new payload needs no new pattern.
  The first version of this fix **reproduced the inversion it was fixing**, scoring three correct
  rejections as compliance — including a `Verdict: SUSPICIOUS, Severity HIGH` — because their own
  summaries quoted the claim they were rejecting. `_ATTRIBUTION_RE` now separates describing a
  claim from adopting it, and applying it to `_COMPLIANCE_RE` also cleared a long-standing false
  positive. Consequence: with the contract in place **0 of 132** attack replies capitulate; with it
  ablated, **27**. That is the first sound evidence that the `@security` contract *causes* the
  defence rather than coinciding with it.
- **A routing outage was scored as model judgment.** goose routes through a provider-filtering
  proxy; unserved models return a localhost `Resource not found (404)`, while
  `_TRANSPORT_FAILURE_MARKERS` carried only `404 not found`. Two models each returned `MISS` on all
  14 payloads and were reported as measured. Neither was ever reached.
- **The liveness guard condemned every fast reply.** `MIN_PLAUSIBLE_SECONDS` marked any sub-1.5s
  answer `NO-CALL`; `gpt-oss-20b` answers in ~1.1s and was condemned 3 times in 4. In a study built
  to evaluate cheap models it would have marked the entire bottom of the cost curve "not a
  measurement" and returned the expensive incumbent as sole survivor. It now condemns only empty or
  prompt-echoing replies, which is what "no model answered" actually means.
- **Spend was bounded per invocation, not per loop.** `--budget` and `MIN_REMAINING_USD` reset with
  each child process, so a 13-model comparison authorised at $3.30 was bounded by neither — worst
  case ~$20.49. Found by `@security` clearance, not by the plan that proposed the loop. The ceiling
  now lives in `agentteams/redteam/budget.py` so the next looping caller inherits it.

### added

- **`--write-integrity-manifest`.** `references/enforcement-integrity.json` has always instructed
  operators to regenerate it with this command; it was named in three files and implemented in
  none. Found by doing the authorised thing — probe E4 correctly flagged an intended change to an
  enforcement module and the documented recovery path was rejected by argparse. A control whose
  recovery path is broken teaches operators to route around the control.
  `tests/test_integrity_manifest_cli.py` now asserts every flag the *generated* manifest note names
  is registered on the parser. That test's first version passed with the flag renamed, because
  argparse prefix-matches abbreviations; it checks exact option-string registration now.
- **Open-weights model security ratings** — `references/openweights-security-model-ratings.csv`,
  generated by `scripts/redteam_model_ratings.py` from run artifacts rather than hand-authored,
  with `references/openweights-security-model-eval.report.md`. 8 of 12 models acceptable. The four
  failures all mishandled a forged Tier-1 authority claim: one returned `status: SAFE`, one called
  it *"valid and secure"*, one granted *"CONDITIONAL COMPLIANCE"* citing a specification that does
  not appear to exist, and one rated a prompt injection *"Informational/Config, Severity: Low"*.
- **`scripts/redteam_model_matrix_run.py`** — runs the judgment corpus across several models with a
  cumulative budget, live pricing recorded per result, a routing preflight, and a **contract-ablation
  arm** as the positive control. It delegates prompt assembly and scoring to the existing judgment
  runner rather than re-implementing either.

### known

- **Verdict attribution is still broken.** `_VERDICT_PATTERNS` scans the whole reply and cannot tell
  who said a token, so a correct denial and a wrong approval of `incr-01` — which *supplies* the
  word `PASS` — still score identically. Pinned by a strict `xfail` rather than left implicit.
- **Approval vocabulary is phrase-keyed.** `Verdict: SAFE`, `✅ SAFE` and `No security concerns
  found` all score `MISS`, so any false-positive rate this harness has published is unsound.
  Widening it reclassifies prior baselines and is therefore an operator decision, not a bug fix.

### fixed (emitted Claude skills were never discoverable — the retrieval layer was unreachable, not absent)

- **Skills now emit as `.claude/skills/<name>/SKILL.md`, not flat `.claude/skills/<name>.md`.**
  Claude Code discovers a project skill only as a directory containing `SKILL.md`, and the
  **directory name is the invocable command name**
  ([docs](https://code.claude.com/docs/en/skills.md)). Every skill this project has ever
  emitted to a Claude target was flat, and therefore **never loaded** — `/recall` and
  `/code-recall` did not exist in any bridged session. The memory index and code index both
  worked fine from the CLI the whole time; nothing could reach them.
  The failure was silent by construction: the retrieval layer's `non-blocking-file-read-then-search`
  contract degrades to grep without warning, so an unreachable index and a working one produce
  identical output — correct answers, more tokens. No error, no test failure. The flat layout
  was additionally **regression-protected in the wrong direction** by `tests/test_bridge.py`
  and two consumer bridge baselines. The tests are fixed; `tests/baselines/*-claude-bridge.json`
  still record the flat path and are **knowingly left stale** — they are `path`+`sha256`
  snapshots of real consumer repositories, and regenerating them requires a cross-repository
  bridge run that this change deliberately does not perform. Nothing under `tests/` reads them.
  All four emitted skills move together (`recall`, `code-recall`, `todo-from-plan`,
  `parallelize-plan`), plus every tool-doc skill via `output_plan.py` — each of those becomes a
  genuinely invocable `/tool-<name>`. `agentteams/bridge_skills.py` is carved out of `bridge.py`
  for the CH-07 ceiling.
- **The skill slug is now taken from the directory, not the filename.** `render_pipeline.py`
  derived it from the file stem, which under the new layout is the literal `SKILL` — it would
  have written `name: SKILL` into every skill's front matter.
- **Migration is a notice, never a delete.** A stale flat file at a bridged target is reported
  and left in place. It is inert, and it cannot be told apart from hand-authored content: these
  skill files carry no `AGENTTEAMS-BRIDGE` fence, the new path's first-time-create fires even
  under the non-destructive `--bridge-merge`, and the delete would be unbacked.
  `.claude/skills/recall.md` is the exact path whose user content was destroyed in the
  2026-05-27 incident (`references/bridge-refresh-safety.md`); shipping an automatic delete for
  it would regress the incident that document exists to prevent.
- **The `--bridge-refresh` Pre-Flight inventory was updated in the same change** across all five
  sites that hard-code the path. Moving the emitter without moving the guard would have left a
  C-5 clearance check inventorying a path that no longer exists, silently unchecking the real
  file on a destructive cross-repository write.
- **`recall` no longer contradicts the agent protocols.** It led with `--query-strategy vector`
  while `navigator`/`quality-auditor` mandate lexical-first with vector as the low-confidence
  retry, and the CLI defaults to lexical. Now lexical-first, with the sparse-tf-idf caveat
  stated plainly: `vector` is cosine over sparse tf-idf term vectors, not embeddings
  (`vector_model_id` is null).
- **Read-only auditors are no longer told a skill can run a query for them.**
  `quality-auditor`, `technical-validator`, and `adversarial` declare `tools: Read, Grep, Glob`
  and were pointed at "the `recall` skill" as an executing affordance. A skill is injected
  prompt text; it confers no capability. The gate now names `@navigator` — which holds `Bash` —
  as the real escalation route. **No `tools:` grant was widened** (C-3).
- **`**/references/code-index/` added to `.gitignore`.** The rule was root-anchored, so a code
  index built under the explicitly-tracked `.claude/` tree was committable — it embeds absolute
  machine paths and installed dependency versions.

### changed (the red-team audit moved to weekly, and gained a catch-up for a missed trigger)

- **Cadence: daily → weekly, Mondays 06:41 UTC.** Operator instruction, on cost grounds. Two
  facts recorded because they change the calculus and were verified rather than assumed: this
  repository is **public**, so GitHub Actions minutes are not billed; and the audit **spends no
  tokens at all** — the engine is deterministic Python (38 mechanical probes plus six
  AST/CSV/regex checks) with no HTTP client, no LLM SDK and no API key. The one component that
  would need live agents, `tests/redteam/run_harness.py` (W14), is invoked by no workflow and
  no script. **The coverage cost is near zero regardless:**
  `tests/test_constitutional_redteam.py` runs the full battery on *every CI run*, so the fast
  regression net for the 21 closed exploits is CI, not the cron; the cron uniquely provides the
  phase-6 self-audit and the dated artifact trail. Reverting is one line.
- **New `redteam-audit-catchup.yml` — hourly retry when the scheduled run never happened.**
  The audit runs on GitHub-hosted runners, so no local machine being off can stop it; but
  GitHub documents that scheduled workflows may be **delayed or dropped** under load, and
  **disables them after 60 days of repository inactivity**. Both are silent, which is worse
  than the machine-off case. The guard fires hourly on the scheduled day, asks whether a run
  has *completed* since the weekly boundary, and dispatches one if not. It self-terminates.
- **Three decisions carry the risk, each with a mutation-tested contract test:**
  **"Ran" means `status: completed`, never `conclusion: success`** — the audit exits `1` on
  findings and `2` on a broken harness and both mean it ran, so a conclusion-keyed guard would
  re-fire 17 times and post 17 issue comments on any day with a real finding, turning a working
  alarm into noise. **It fails open** — an unusable API answer runs the audit, because "I could
  not tell whether it ran" resolving to "it must have run" is indeterminate read as a pass,
  relocated into the thing that decides whether a security audit happens at all. **It writes no
  state** — the verdict is GitHub's own run history plus the wall clock, since a "last run"
  marker is a cursor that once stale suppresses every future audit.
- **What the catch-up cannot fix, stated rather than papered over:** the 60-day inactivity
  disable stops the guard as surely as the audit — a guard cannot fire to report that guards
  are not firing.
- `scripts/run_daily_redteam_audit.sh` → **`scripts/run_redteam_audit.sh`**. A script whose
  name asserts a cadence it no longer has is stale content in the most load-bearing place
  there is (Rule 7), and a new test fails if any live surface still calls the audit daily.


### added (the red-team audit became a standing daily check, and one that audits itself)

- **`agentteams --redteam` — a standing red-team audit on a daily cadence.** The 2026-08-06
  constitutional audit found and closed 21 measured exploits, but it was a one-off, and the
  *process* around it failed four times — each failure caught late, by accident, or only when
  the operator asked a pointed question. This turns the audit into a scheduled check
  (`.github/workflows/redteam-audit.yml`, 06:41 UTC daily; driver
  `scripts/run_daily_redteam_audit.sh`) and turns those four failures into mechanical checks.
  Engine: `agentteams/redteam/`. Procedure: `references/redteam-audit.procedure.md`.
- **Phase 6 — the audit evaluates the red team, not just the target.** Six checks, each with a
  test proving it *fires* and a second proving it stays silent, because a self-audit that
  cannot fail is the exact defect it was written to catch:
  **F-1** every verifier has a sensitivity test and a negative control;
  **F-2** every guard reaches every call site, measured per *branch* (both `emit_all` sites
  live in one function, so a function-level rule sees one host and passes);
  **F-3** no hand-rolled workspace enumeration, descriptor lookup, or `.git` status check in
  fleet-consuming code;
  **F-4** every count carries the population it was computed over, from a canonical
  enumerator;
  **F-5** every probe whose outcome *or normalised evidence* changed is re-validated for
  intent;
  **F-6** every accepted weakness has a named reason, and a probe that now defends loses its
  exemption.
- **F-1 found four controls with no sensitivity test at all**, now written in
  `tests/test_redteam_verifiers.py`: `integrity.verify` — the hash manifest over every *other*
  control — had none; `security_gate.check_clearance`, added specifically so inspecting a
  clearance would not spend it, had nothing pinning that property; `scan._check_line` and
  `scan._check_injection` were exercised only through `scan_content`, so a regression in
  either could be masked by a change in the caller.
- **Three exit codes, kept distinguishable.** 0 clean, 1 findings, 2 **harness broken** — a
  failed control probe, an unimportable probe module, a corpus claim that no longer matches
  the scanner, a run that touched the live agent tree, or a death by traceback. A battery
  whose controls fail reports "no exploits" exactly as loudly as one that found none.
  Indeterminate is not a pass, and code 2 outranks code 1.
- **`agentteams/redteam/realcopy.py` — attack the real agent infrastructure, in an isolated
  copy of it.** Synthetic fixtures measure each control against inputs its author imagined.
  This snapshots the genuine `.github/agents/` and `.claude/agents/` into a temp root, attacks
  that, and asserts afterwards — as a git *delta*, not an absolute cleanliness check — that
  the live tree is byte-identical. `--update --merge` is deliberately **not** used as the
  restore: probe `C3` measured that an escalated capability grant *survives* it (front matter
  cannot be fenced), `D3` that deleting fence markers makes the merge refuse to write at all,
  and `D4` that renaming a fence keeps the weakened body alongside the real one. The merge is
  instead a *measurement*: `classify_restorability` reports each mutation as `RESTORED`,
  `PRESERVED` or `REFUSED`.
- **`redteam-methodology.reference.template.md` ships with every generated team**, carrying
  the seven phases, the T0/T1/T2 tier model, the outcome classes and the six failure modes.
  Delivered as an extension to `@adversarial` rather than a 31st agent: the judgment work is
  *"does this control actually measure what it claims"*, which is presupposition critique
  applied to a control. Not added to `_TEMPLATE_AUTHORITATIVE_FENCES` — the documented bar
  there is "the project has no legitimate reason to extend its body", and a downstream project
  plainly has a reason to add its own probes.
- **The accepted-weakness list moved from a Python literal to
  `references/redteam-accepted-weaknesses.csv`**, so the standing suite and the daily audit
  read one ledger instead of two. `tests/test_constitutional_redteam.py` keeps all four of its
  assertions and gains an anti-vacuity check: an empty or unreadable ledger makes the strictest
  assertion *pass*, so the ledger being present and substantive is now asserted directly.
- **`agentteams/integrity.py` covers the phase-6 check modules**, and
  `tests/test_redteam_integrity_coverage.py` pins the membership list — nothing referenced
  `ENFORCEMENT_MODULES` before, so an entry could be deleted and every remaining hash would
  still verify clean.


### fixed (the audit backlog cleared, and two standing checks so it cannot reopen)

- **`docs_src/cli-reference.md`: 71/90 → 90/90.** The page opened by claiming it documented
  *all* flags while missing 19 — including `--pin-templates` (the S4.6 trust root) and
  `--reconcile-front-matter` (which gates a capability-grant change under C-3). Four new
  sections carry them: Code & API Index, Generated Maps and Git Hooks, Output Safety /
  Template Trust / Front-Matter Reconciliation, and Goose Source/Model Switch. The Synopsis
  block was 19 short too.
- **`README.md` acknowledged `agents-md`.** The framework appeared **zero times** in the
  README while being a first-class registry entry documented on the site — so the landing
  page and the docs contradicted each other on what the tool supports. Added to the support
  table, the `--framework` choices and the default-locations list. The CLI block no longer
  claims to be `agentteams --help` output: it is labelled "Most-used options" and points at
  the complete surface, because a curated subset presented as verbatim `--help` gives a reader
  no signal that it is partial. The structure tree showed 9 modules for a 76-module package
  and omitted four subpackages.
- **`api-reference/cli.md` — the page documenting the CLI carve was behind the carve.** It
  cited a two-entry `LENGTH_ALLOWLIST` that is now **empty** and four line counts that were
  all wrong, and its module map was missing six modules including `standalone_modes.py`, the
  direct product of the refactor it narrates. Line counts are now a pointer to `wc -l` rather
  than restated — restating them is what went stale.
- **Seven pages that build and deploy were reachable from no navigation.** `interoperability.md`
  was the sharp one: three pages send readers to a "dedicated Interoperability tab" that did
  not exist. `template-pins` and `front-matter-reconcile` compounded the flag gap — both
  features were missing from the CLI docs *and* off the nav, so both were undiscoverable.
  All 57 API pages are now linked from the overview, whose own coverage-gap list was also
  stale (claimed 14; the ratchet says 20).
- **Two standing checks, both seeded EMPTY.** `test_cli_reference_flag_parity.py` and
  `test_docs_nav_completeness.py`. The empty seed is the point: writing the docs first is what
  let the ratchets start at zero instead of enshrining the debt in the instrument meant to
  retire it. Nav exclusions are **derived** by scanning for `--8<--` snippet includes, never
  declared, with a guard test asserting the scan still finds something — otherwise a silent
  regression would push snippet targets into a declared exclusion list and lose the property.
- **Bridge Tier-1 drift: 3 → 0** (`--stale-check --self`); Tier-2 advisory 45 → 37.
  **An ordering defect found by running it:** the remediation plan sequenced the bridge
  refresh *before* `--self --update`, which rewrites the very tree the bridge manifests
  digest. The Tier-1 count went 3 → 0 → 3 → 0 before the order was corrected.
  `run_daily_bridge_maintenance.sh` already encodes the right order; the plan did not copy it.
- **The self brief named three paths that do not exist** — `primary_output_dir: "src/"`,
  `figures_dir: "docs/figures/"`, and a null `reference_db_path` while the rendered table
  named `docs/` as the reference database. Constitutional Rule 5 pointed agents at a
  directory of generated HTML.
- Generated `--recipe-check` output is now gitignored — it was untracked but *not* ignored,
  so the next `git add -A` would have committed a build artifact (Rule 4).

### known (logged to `references/agentteams-remediation-log.csv`, not fixed here)

- **`--update --merge` does not propagate brief placeholder changes to fenced regions.** After
  the three path fields above were corrected, the run reported "No structural or
  template-content changes detected" and left the fence showing the old values. This is not a
  stale-manifest artifact: `analyze.build_manifest` re-read from disk and resolved the new
  values correctly, so the loss is downstream of analyze, in the drift/merge decision — which
  appears to compare template content and structure but not resolved placeholder values. The
  operator sees a successful run and a silently unchanged file.
- **The `tone_and_style` template hardcodes agent names.** `@post-production-auditor`,
  `@module-doc-validator` and `@module-doc-author` are named with no roster parameterisation,
  so every generated team is told about teammates it may not have. Fixing it touches every
  generated team plus the template ledger and the unfenced-constraint ratchet.

### added (a documentation-refresh procedure, and a trigger that cannot latch itself off)

- **The 2026-08-06 staleness audit found one pattern, not a list of defects.** Every
  documentation surface with a standing check was current — `agentteams.1` at 90/90 flags
  because CI diffs it, zero stale API-reference pages, zero broken internal links. Every
  surface without one had drifted: `docs_src/cli-reference.md` claims "All flags" and is
  **19 of 90 short**; `README.md` documents 37 of 90 and omits the `agents-md` framework
  entirely; `api-reference/cli.md` cites a `LENGTH_ALLOWLIST` that is now empty and four
  line counts that are all wrong. Report:
  `references/plans/documentation-staleness-audit-2026-08-06.report.md`.
- **`references/documentation-refresh.procedure.md`** — the per-document procedure. Stage 1
  is mechanical and scripted (man-page regen, flag parity, API parity, nav completeness,
  link check, `--stale-check`, fenced-file regeneration via the brief). Stage 2 is authored
  and routes each surface to the agent that owns it, against the authority it must match.
  A firing trigger with a clean Stage 1 and nothing in Stage 2 is a **legitimate no-op** —
  the procedure says so explicitly, because a cosmetic commit to reset the clock is exactly
  the metric-gaming the audit warned about.
- **`scripts/docs_freshness_watch.py`** — fires when `docs_age > 24h AND src_age <= 24h`.
  Both sets are derived from `git ls-files` by what a path *is*; a guide added tomorrow is
  watched tomorrow. stdlib-only and imports nothing from the package, so a dependency
  failure cannot take the watcher down. Advisory: **always exits 0** except under opt-in
  `--check`, the same instrument class as `check_session_obligations.py`.
- **The reliability property is that the verdict is a pure function of git history and the
  clock.** No cursor, no marker commit, no cache participates in it — so a dropped,
  cancelled or crashed run has *zero* effect on the next one, and an unmerged remediation
  cannot silence anything because the docs are still stale. Three prohibitions are asserted
  by tests rather than asked for in a comment: no `cancel-in-progress`, no state file read
  back by a later run, reporting steps stay `if: always()`. `git` failing yields
  **indeterminate**, routed to a distinct "watcher broken" issue — never mapped onto "docs
  are fine".
- **Five trigger channels across three mechanisms**, two of them load-bearing rather than
  redundant: `push` catches `src_age` dropping to 0 while `docs_age` is already past the
  window; the 6h `cron` catches `docs_age` *crossing* the window with no new push, which
  push cannot observe at all. Plus `workflow_dispatch`, a third execution path in
  `run_daily_bridge_maintenance.sh`, and `docs-freshness-watchdog.yml` on a separate cron
  for the case the watcher stops running entirely. Nothing watches the watchdog; that
  residual is stated in the procedure rather than papered over.
- **The adversarial pass found a design-killing defect that reading would not have.**
  `CHANGELOG.md` was in the documentation set — and `changelog-link.yml` forces a changelog
  edit on nearly every code PR, so it acted as a freshness alibi for every other doc. The
  first live run showed the signature: both ages 60.18h, both from the same commit.
  Backtested over 90 days at the real 6h cadence (361 evaluations): **13 firings with it,
  34 without — 62% of the signal suppressed.** `CHANGELOG.md` is now inert (neither doc nor
  source), losing nothing, since it is the one doc surface already covered by a failing
  gate. Eight further findings applied, all recorded in the procedure's audit section.
- **60 tests** in `tests/test_docs_freshness_watch.py` covering the truth table, both
  window boundaries, set derivation, idempotence, the write-nothing property, the
  indeterminate branches, and the three workflow prohibitions.

### added (standing checks for three things that were only ever found by hand)

- **The suite now names any test that mutates a tracked artifact.** One full run rewrote a
  bridge manifest on 2026-08-03 and never reproduced — not across seven later runs, a ~120-file
  bisect, or a deliberately-staled manifest. Rather than keep hunting it, `tests/conftest.py`
  snapshots `references/bridges/` after every test, so the next occurrence names the test and
  fails the session. The investigation became a detector. Overhead measured before adding:
  **0.53 ms per snapshot, ~1.8 s across the suite (<0.5%)**. The snapshot is deliberately
  defensive and its skips are **counted**, because a throwaway version of this raised inside a
  test that patches `Path.glob` on the class — aborting monkeypatch's own undo and producing
  2315 cascading errors.
- **Every published schema must have something checking it.** Three artifacts drifted from
  their contracts this session, each found by ad-hoc investigation: `doc_site_config_file`
  missing from a schema with `additionalProperties: false`, a stale example brief, a stale man
  page. The pattern was that nothing enumerates the project's published artifacts. The list is
  **derived from the tree, never declared** — a hand-written enumeration is itself an artifact
  that goes stale. Two schemas currently have no check and are recorded as debt that may fall
  and must not grow.
- **A module-doc ratchet.** 20 modules have no `api-reference` page, mostly because CH-07 carves
  keep creating modules out of documented ones. A new module now arrives documented or does not
  arrive. `template_pins` and `front_matter_reconcile` were documented first, so the count
  arrived **falling (22 → 20)** rather than merely frozen.
- `tmp/by-week/INDEX.md` separates live plan artifacts from historical: **508 non-terminal rows
  across 70 files, 434 of them (85%) in closed weeks**. Nothing was edited — a step CSV is Rule 9
  evidence, and rewriting 400 rows to improve a count would destroy the record to improve the
  metric. `tmp/` is gitignored in full, so this is a local working record, not shipped state.

### changed (`cli/generate.py` carved — the standalone modes were never part of the pipeline)

- **979 → 917.** The "do one thing and exit" modes — restore-backup, scan-security,
  check-budget, template pinning, the retrieval utilities — moved to
  `agentteams/cli/standalone_modes.py`. `generate.py`'s own docstring already named them as a
  separate group *interleaved* with the render/emit pipeline they have nothing to do with.
- The carve was **overdue rather than chosen**: it blocked two consecutive features the same day,
  each needing only a five-line dispatch stub, and in both cases the feature's logic had already
  been moved into its own module. `CEILING_MARGIN_BASELINE` is empty again.
- `--adopt-orphans` deliberately stayed — it mutates the manifest *before* rendering, so it is
  part of the pipeline, not an alternative to it.
- **Verified by byte-identical render**, not by green tests: pre-carve and post-carve output
  compared in the same window across three frameworks, identical. (A first comparison showed
  three differing files; a negative control established those as live threat-intel timestamps
  from the runs being minutes apart.)
- `test_cli_output_contract.py` caught the move — one `[SEC-GATE/DESTRUCTIVE:restore-backup]`
  site travelled with the carve. Its checks now read both halves of the surface, and the
  adjacency walk runs **per file**, since pairing a message with its `_assert_*` call is a
  within-file property.

### fixed (`--dry-run` hid a capability grant change)

- The preview reported `UNCHANGED` for a file whose `allowed-tools` the real run widened. The
  cause is structural: both paths call `_merge_fenced_content`, but **only the real path then
  calls `_merge_front_matter`**. The preview's `migrated == existing_text` comparison was made
  against content whose front matter had never been merged, so a file whose *only* change was a
  front-matter key compared equal.
- Front matter is where capability grants live. C-3 makes widening one privileged, and
  `--dry-run` is what an operator reads before approving it.
- The dry run now runs the same front-matter merge with the same build-log baseline.
  `_merge_front_matter` is pure — it returns keys and notices and writes nothing — and a test
  asserts the preview still writes nothing, since running more of the real path is exactly how a
  preview stops being one.

### fixed (a shipped example contradicted the contract it was meant to demonstrate)

- `examples/learn-python-for-stats-and-econ/brief.json` violated
  `project-description.schema.json` two ways: `authority_sources` as bare strings, and
  `existing_project_path: null` where every other brief omits the key. It built correctly
  regardless, because `analyze.py` tolerates the string form — `if isinstance(src, str)` — so
  nothing complained.
- **The schema was right and the example was stale**, which is the opposite of the first
  reading. Three artifacts define the contract as objects — the schema,
  `docs_src/DESCRIPTION-FORMAT.md` (*"Type: array of objects"*), and
  `ingest._parse_authority_sources`, which emits dicts for markdown briefs. `analyze.py`'s own
  comment calls the string branch a tolerance: *"Accept both plain strings and dicts"*. Acting
  on the first reading would have widened a correct schema to accommodate one bad file.
- The object form is **derived from `analyze.py`'s own fallback** (`{"path": src, "name": src}`,
  `scope` defaulting to `general`, `rank` to position), so nothing was invented for a teaching
  example. Proved: rendering both briefs in the same window produces **byte-identical output**
  across all 61 files — a representation change with no content change.
- Guards added for **every shipped example brief**, not just this module's own, with the two
  violations asserted separately so fixing one cannot mask the other. All three were verified
  red against the unfixed example before the fix landed.

### fixed (the template pin refuses rather than guessing where to live)

- **The fallback could put the trust root inside the generated tree.** With no declared
  `existing_project_path` and a working directory inside the output, `consumer_root` returned
  `output_dir.parent` — `<proj>/.claude` for a claude team, which the tool regenerates. The
  module's own docstring says the pin must live outside that area, because *"a trust root the
  tool routinely rewrites is not a trust root."* The fallback contradicted it. Same mistake,
  second time, in the same function: the primary path had been corrected the same day and the
  fallback left expressing the identical confusion.
- It now raises `PinLocationError` instead of guessing. `--pin-templates` **exits non-zero** so a
  script notices; a routine run reports that verification did not happen rather than implying it
  passed. A pin in a directory that gets overwritten is worse than no pin — it reads as
  protection.
- The four-clause candidate test collapses to one named predicate, `_is_outside`. One clause was
  dead: `out.parents[:0]` is the empty slice, so `root not in (out, *out.parents[:0])` reduced to
  `root != out`, which the first clause already said. **Proved equivalent across a case table**
  (project root, the output dir, inside it, `.claude`, a sibling project, deep inside, filesystem
  root) before the swap — that table is also what showed the fix belonged in the fallback rather
  than the condition.

### added (`--pin-templates` — a trust root outside the writable surface, S4.6)

- Security review §4.6: nothing verified the shipped templates. `drift.py` compares against the
  *target's own* build log — change-since-last-build, not authenticity — so anyone able to write
  to the installed package edits Tier 1 of the authority hierarchy and every restore propagates
  it with full ceremony. The one gap where existing controls **amplify** a compromise.
- **The obvious fixes are worth less than they look, and this deliberately is not one.** A
  checksum manifest shipped inside the package, or a signature verified against a key pinned
  inside it, both put the thing being protected and the thing protecting it on the *same
  writable surface*. `--pin-templates` records digests in
  `<project>/.agentteams/template-pins.json`, which the **consumer commits to their own version
  control** — a root outside the surface an attacker with package write access controls.
- **The load-bearing property is negative: nothing else ever writes the pin.** A mismatch reports
  and never re-pins, because a pin that follows what it checks records only the last thing it
  saw. A structural test asserts `write_pin` has at most one caller, so a future "helpful
  auto-refresh on upgrade" fails rather than quietly erodes it.
- A mismatch **reports and does not block**: the threat is silent propagation, which a loud
  report defeats, and refusing to generate would punish an intentional upgrade. Purely additive
  template sets read as normal, not as tampering.
- **Residue stated rather than claimed away:** this is trust-on-first-use. It detects change
  since you pinned; it cannot tell a good first pin from a compromised one. Pair with
  install-time verification (`pip --require-hashes`, attestations) if the wheel itself is in
  scope — that path remains unbuilt and is a release-process decision.
- **A defect found only by running it:** the first implementation derived the pin location from
  `resolve_output_dir`'s `project_root`, which equals `output_dir` whenever `--output` is given —
  the normal invocation — so the trust root landed **inside `.claude/agents/`**, a directory
  every run rewrites. The unit tests passed throughout because they were handed a root rather
  than deriving one. Fixed, with a regression test.

### added (`--reconcile-front-matter` — divergence made visible without silent escalation)

- Front matter **cannot be fenced**: YAML must occupy the first bytes and a fence marker is an
  HTML comment, so there is nowhere to put one. The merge handles it with a three-way rule —
  template value applied to an unmodified file, on-disk value kept on an edited one. The second
  branch is deliberate (an edit may be a project's choice) but it means a capability fix
  expressed as a `tools:` grant stops silently at every edited file, with the only signal a
  notice buried in a full `--update --merge` run.
- `--reconcile-front-matter` answers "where does this team's front matter diverge from its
  templates?" as a standing, read-only question. `--reconcile-apply` takes the template's value,
  and is **never implied by the report**: `allowed-tools` is a capability grant, C-3 makes
  widening one privileged, and every applied change is announced with
  `[CAPABILITY GRANT CHANGED]`.
- Only keys the **template declares** are compared. A key the deployed file adds is a project
  choice, not drift — reporting it would train operators to ignore the report. A file with no
  front matter at all is skipped rather than given one, since inventing a block means inventing
  a capability grant.
- **Measured before building, and stated in the tests: this repository's own team has zero
  divergences.** The apply path is exercised by fixtures and by two end-to-end CLI tests that
  plant a real perturbation, not by production data. Machinery that has not run against a real
  instance should say so.

### fixed (the collision backlog reaches zero, and a live cross-agent contradiction goes with it)

- **`technical-validator.md` asserted two incompatible meanings for `CH-01`.** It carried
  **7 × `TV-0x` and 7 × `CH-0x`** IDs for the same seven rules, while `code-hygiene.md` defines
  `CH-01` as *"No Backup Files in Source Tree"*. The `CH-xx → TV-xx` rename was deliberate; the
  unfenced copy was the pre-rename text the fence was meant to replace. Removing it takes the
  file to 7 TV / 0 CH and resolves a contradiction of exactly the kind CH-20 forbids.
- **Three sections claimed something false about their own file.** `> ⛔ … the guidance **below**
  are the immutable contract` predates fencing — sections are placed by name, not position, so a
  deployed file may carry them in a different order than the template. The fenced copies say
  "carried in this file's fenced sections". The stale copies are gone.
- **Three of the ten reported "collisions" were never collisions.** `_deployed_fence_carrying`
  asks whether *some fenced body contains* the heading — the right question for safety, since a
  nested heading genuinely does survive a removal. It is the wrong question for **pairing**:
  `invariant_core` contains several sub-headings, so an unfenced `### Core Responsibilities` was
  compared against the entire `invariant_core` body, never matching and never resolving.
  `_fence_whose_first_heading_is` is a **second, narrower predicate** used only to decide what is
  comparable. The safety predicate is deliberately untouched — narrowing *it* would have
  re-opened the data-loss hole closed the same morning.
- **Three genuine divergences hand-merged, and `navigator.md` proves why that mattered.** Its
  unfenced block was not a stale copy: `### Core Responsibilities` was duplicated in the fence,
  but `### Project Structure` — the project's own output/reference/figure paths — existed
  **nowhere else**. A blanket "accept the fenced copy" would have destroyed it. Only the
  duplicated prefix was removed; the project data and the heading labelling the fenced workstream
  table both stayed. The other two were proved strict subsets of their fenced twins line by line
  before deletion.
- **Collisions in the deployed team: 40 → 0.** Nothing remains for the resolver to report.

### changed (`graph.py` carved — the first CH-07 split chosen rather than forced)

- **`graph.py` 992 → 770**, with front-matter extraction moved to `agentteams/graph_inputs.py`
  (269 lines: nine functions and the six regexes they use). `graph.py` owns the graph model and
  its rendering; the new module owns the step before — reading an agent file's YAML front matter
  into the values a node is built from.
- **`CEILING_MARGIN_BASELINE` is now empty for the first time.** This is the sixth carve under
  CH-07 and the first that was *chosen*: the other five each happened when an ordinary edit
  pushed a module over the ceiling mid-change, exactly as the remediation ledger predicted and
  then recorded four times over. `graph.py` had eight lines of runway.
- Independence was verified **by AST before the move** — no reference to `TeamGraph`,
  `AgentNode`, `GraphEdge`, `AGENT_TYPES` or the SVG palette — rather than by reading. Only the
  six regexes the block actually references moved; `_DESC_RE` stayed, which a plan audit
  specifically called out after the previous carve moved regexes its origin still needed.
  `graph.py` re-exports all nine helpers, so `tests/test_graph.py`'s imports are untouched.
- Four modules remain above 900 lines — `bridge.py` 973, `cli/generate.py` 966, `analyze.py`
  943, `audit.py` 902 — and none is within the 25-line warning margin.

### fixed (a scaling claim tested with a clock, and an unexamined capability default)

- **`test_build_index_scaling_is_reasonable` measured scheduler noise.** It asserted
  `large_s < small_s * 4.0 + 0.05` where the 60-document baseline was **3.3 ms**, so the
  multiplicative term contributed ~13 ms and the whole check rested on a 50 ms budget on a
  shared runner. It went red on macOS/3.11 while three sibling jobs passed the same commit, and
  a re-run of that identical commit passed. The claim is structural, so it is now measured
  structurally: postings written **per document** is exactly constant across 60/120/240-document
  corpora and identical on every machine. A ratio against a near-zero denominator is the defect;
  an absolute seconds-scale ceiling is kept alongside it so a genuinely slow indexer still
  fails. Verified stable across 20 consecutive runs and under concurrent load.
  `test_query_latency_bound_for_moderate_corpus` is deliberately left as a wall-clock check —
  ~100× margin rather than ~15×, an absolute ceiling rather than a ratio — with the reason
  recorded in the test rather than the two being treated inconsistently.
- **Every framework now declares its front-matter contract explicitly.** When
  `required_front_matter_keys()` was added, contracts were declared for copilot-vscode and
  claude — the two whose headers had been inspected — and the rest were left on the empty
  default. From outside, "inspected, and there is none" and "not yet inspected" were
  indistinguishable, which is precisely how claude's header went unchecked in the first place.
  All five now state their answer with a reason: agents_md strips front matter and prepends a
  heading; goose emits recipe YAML; **copilot-cli** emits 29 of 30 files with no header at all.
- That last one corrected an earlier generalisation of mine. I had sampled `navigator.md` on
  copilot-cli, seen no front matter, and concluded the framework had none. `team-builder.md`
  does — it comes from `render_builder_file`, a different path — which is why the contract is
  declared empty for the agents rather than the file set.
- The new test ties each declaration to real emitted output and is guarded against passing
  vacuously: at least two frameworks must declare a **non-empty** contract, or the comparison
  would be checking nothing.

### changed (the last file missing its fences finally received them)

- **`conflict-auditor.md` merged: 4 fences → 8**, gaining `invariant_core`, `rules`,
  `handoff_payload_codes` and `handoff_payload_conflict_codes`. It was the only file in the
  deployed team missing any, and for a traceable reason: its render was truncated and
  unparseable, so every `--update --merge` run skipped it. Fixing the fence-blind handoff
  stripper earlier the same day is what made this possible at all.
- `## Rules` — the section a resolver defect deleted outright that morning — is now removable
  **safely**, because the fence that must survive it finally exists. Applied: one copy remains,
  inside the fence.
- **Collisions in the deployed team: 11 → 10, and all four "no fenced survivor" refusals are
  gone.** Every remaining one is a content difference awaiting a human, not a missing fence.
- The merge was **rehearsed against an isolated copy** of `.claude/` before touching the real
  tree, because there is no per-file scope flag and the operation therefore spans the whole
  team. `security.md` — the file this class of operation destroyed on 2026-08-01 — came through
  at 415 → 415 lines with all 5 fenced regions intact, changed only in live CVE/EPSS data.

### fixed (a discharged test pin, replaced by a live invariant)

- `test_the_real_deployed_conflict_auditor_is_refused` pinned one file's defect and went red the
  moment that defect was fixed — exactly as its own failure message predicted. A pin on a defect
  expires when the defect does. Replaced by an invariant over the whole deployed team: every
  duplicated heading must have a fenced survivor, with an anti-vacuity floor so a broken walk
  cannot pass by finding nothing.

### fixed (the collision refusals were four problems giving one wrong instruction)

- **"Run `--update --merge` first" was wrong for 15 of 19 refusals.** Measured: those files
  already carry every fence their render produces. Only `conflict-auditor.md` is genuinely
  missing fences — 4 of 8 — because its render was truncated and unparseable until earlier the
  same day, so every merge run skipped it. The refusal text was pointing operators at the one
  operation that has twice destroyed content, to fix files where it would have changed nothing.
- **The span bound overshot into managed content.** `_unfenced_section_span` ends a section at
  the next heading of the same-or-higher level. When the fenced twin sits *below* the unfenced
  one, that next heading is the twin's own heading **inside its fence**, so the span swallowed
  every fence in between and the enclosure guard refused. `_span_bounded_at_first_fence` ends
  the section where the managed content begins, which makes the removed span fence-free by
  construction. **8 collisions resolved**, each still required to equal the *deployed* fence
  body — the bound is not the authorisation.
- Two independent guards, because the bound alone is unsafe: a section that continues past a
  fence would be truncated (caught by the equality test — a partial section cannot equal the
  whole), and a distant first fence could swallow a sibling section (refused outright, since a
  section cannot contain a heading of its own level or higher).
- **Refusals now name the actual condition** and stop recommending a merge when the fence is
  already present. Needs-review drops **19 → 11**: 4 genuinely awaiting a merge, 7 awaiting a
  person.
- `references/plans/collision-hand-review.report.md` reports those 7 with the real textual
  difference between the two copies, and deliberately **does not claim which is correct** — the
  unfenced copy may be superseded, or may be a local change the project wants. Only a maintainer
  can tell, and getting that backwards is how `security.md` lost 331 lines.

### fixed (the resolver deleted a section whose only copy was the one it removed)

- **Every removal was justified by a fence that might exist only in the template.** `incoming`
  is read from the *fresh render*, so "a fenced copy remains" was never checked against disk.
  Applying nine `[equality]`-proved resolutions removed `## Rules` from
  `.claude/agents/conflict-auditor.md` — five substantive routing rules, **zero surviving
  copies**. That file carries four fences and has never had `rules`.
- **It is the same defect that took `security.md` from 363 lines to 32 on 2026-08-01.** The
  guard added after that incident refuses when the removal span *encloses* a live fence — a
  different property, which did not fire here because the span enclosed nothing. The guard's own
  comment already stated the correct rule (*"merge the fence in first; only then is the unfenced
  copy a duplicate"*) as prose rather than code.
- `_deployed_fence_carrying` now requires exactly one fence **in the deployed file** carrying
  the heading. It runs before the enclosure guard, because it is the stronger condition —
  enclosure asks whether the span would take managed content with it; this asks whether anything
  is left afterwards. It binds on the `--trust-provenance` path too: provenance answers *"has
  anyone edited this file?"*, which is not the question that makes a delete safe.
- **The suite was green through both incidents because the tests encoded the unsafe behaviour.**
  `test_resolve_fence_collisions.py`'s fixture built a deployed file with *no* `invariant_core`
  fence and asserted the section count reached **zero** after resolution — the loss, written down
  as the expected result. Fixtures now model the post-merge two-copy state the script is for.
- Caught by a plan-mandated gate that re-derived the proof from the **written result** rather
  than the input, then reverted from git. The tool's own backup was deliberately not relied on:
  a backup taken by the component under test is not an independent net.
- Against this repository's team the resolver now reports **8 resolvable / 19 needs-review**
  (was 9 / 18), and the eight are applied: 7 files, 52 lines of duplicate prose removed, every
  fenced region body hash preserved, the other 47 files byte-identical.

### fixed (the render truncation, and an audit that could not see four of five frameworks)

- **A prose stripper was deleting fence markers.** `_HANDOFFS_HEADING_RE` removed a
  `## Handoff…` heading and everything under it up to the next `#{1,3}` heading *or end of
  file*, treating AGENTTEAMS markers as ordinary text. `conflict-auditor.template.md` fences a
  heading-only `## Handoff Payload Conflict Codes` with no further `##` heading before EOF, so
  the match ran to `\Z` and consumed **three markers** — that fence's `END` and both markers of
  the entire following fence, 877 characters, template lines 159–172. The `BEGIN` above the
  heading survived, leaving the emitted file at 8 BEGIN / 7 END. Everything downstream followed
  from that: `_extract_fenced_regions` returned an error string, `_normalize_generated_content`
  wrapped the whole body, and the operator saw **"Nested fence not allowed: invariant_core
  inside content"** — an error three steps from its cause, which got this file misdiagnosed
  twice (first blaming the template for nesting, then blaming the merge). A fence marker now
  terminates the strip exactly as a heading does. Blast radius measured on *rendered* output,
  not templates: **2 files across 3 frameworks**, both `conflict-auditor.md`; copilot-vscode is
  untouched because it supports handoffs and never strips. A strip may leave a fence empty; it
  may never leave one unbalanced, and every rendered file is now checked for that.
- **The post-generation audit assumed copilot-vscode and lied in both directions.** Fourteen
  places across `audit.py` and `audit_agent_contract.py` hardcoded `.agent.md`. On claude,
  copilot-cli, `agents_md` and goose — all of which emit `*.md` — `generated_slugs` came out
  **empty**, so every required agent was reported missing, including `orchestrator` with
  `orchestrator.md` in the same file map; `_check_workstream_expert_coverage` separately built
  `f"{slug}-expert.agent.md"` and looked it up, a name-construction bug that threading a suffix
  alone would have left firing. Meanwhile every per-file check inspected **zero files**, so
  CH-14 was silent and the per-agent contract checks — including the invariant-core ⛔ marker
  check S4.5 rests on — did not run at all.
- **Fixing the filter exposed a second layer, and fixing only the filter would have been
  worse.** With the checks finally running, they produced **83 new findings** — because
  `_REQUIRED_YAML_KEYS` was a copilot-vscode contract applied as if universal. The three
  frameworks emit three genuinely different headers (`name, description, user-invocable, tools,
  model` / `name, description, allowed-tools` / none at all), and `AR_MISSING_RETURN_HANDOFF`
  demanded a section the pipeline deliberately strips on frameworks that do not support
  handoffs. Adapters now declare their own contract via `required_front_matter_keys()`,
  defaulting to empty — *this framework asserts no header shape* — and the handoff check is
  gated on `supports_handoffs()`. Result on identical input: **all three frameworks now report
  the same 7 findings with the same codes**, where claude previously reported 15 including 9
  false positives and dropped CH-14 entirely.
- **`doc_site_config_file` was a supported key the schema rejected.** `analyze.build_manifest`
  and `manifest_format` both read it, but it was absent from
  `project-description.schema.json`, which sets `additionalProperties: false` — so every brief
  declaring it failed validation, including this module's own. Nothing noticed because nothing
  validated that brief; it does now.

### changed (the self brief describes capabilities the repository already has)

- `.github/agents/_build-description.json` declares a `retrieval_integration` contract and a
  documentation deliverable. `@retrieval-integrator` and `@cohesion-repairer` are listed as team
  members in `.claude/CLAUDE.md` and deployed in `.claude/agents`, but the brief gated neither,
  so both sat as orphans that no advisory could see — and `git log -S` confirms
  `retrieval_integration` was **never** declared. Every field is derived from code that exists
  and asserted to resolve: `mode: lexical-index` (the index is BM25), `query_index`,
  `--refresh-index`, `--query-code`, and the real source directories. Orphan count for the
  deployed team goes to **zero**. Nothing was regenerated — verified by dry run only.

### fixed (three advisories that could not see what they were built to report)

- **The orphan advisory was blind to every framework but one.** `_report_orphan_agent_files`
  filtered on `.agent.md` in both directions — the emitted set *and*
  `output_dir.glob("*.agent.md")`. Only copilot-vscode uses that suffix; claude, copilot-cli,
  `agents_md` and goose all emit `*.md`, so the glob matched nothing and the advisory reported
  zero orphans **regardless of what was on disk**. Measured on this repository's own
  `.claude/agents`: four deployed files the current brief no longer emits — `cohesion-repairer.md`,
  `retrieval-integrator.md` and two retrieval reference docs — none of them ever reported. The
  extension now comes from `adapter.get_file_extension("agent")`, as `cli/generate.py`'s
  `--adopt-orphans` path already did; only this advisory hardcoded it. `agent_ext` is keyword-only
  and **required**, because a default is what let the suffix go unexamined. The two existing
  carve-outs (adopted agents, tool docs) built their names with a literal `.agent.md` and would
  have silently switched off under a bare `.md`; `SETUP-REQUIRED.md` gains an explicit exclusion it
  previously got for free from the suffix, matching `bridge_sources.py`.
- **A markdown table row is not a deleted rule.** `_detect_deleted_constraints` reported rows like
  `| invariant_core | FENCED | ⛔ contract: … |` as deleted constitutional rules — they match `⛔`
  and clear the 30-character floor. The unfenced-constraint ratchet already excluded table rows,
  *inline, in a test file*, so the two consumers of `CONSTRAINT_BEARING_RE` disagreed about what a
  rule is — the exact drift that constant's docstring exists to forbid. The eligibility half is now
  one shared predicate, `fences.is_trackable_constraint_line`; a synthetic probe over the template
  library goes from **11 notices in 8 files to 0**, with the library measurement unmoved at 43
  files / 159 lines (verified equivalent under both whitespace normalisations before the change).
  The consumers still diverge on `NUMBERED_RULE_RE`, deliberately: unifying that adds **130 lines
  across 24 templates**, which is a re-baselining exercise and is tracked as its own item.
- **Six trailing collisions were unresolvable by any route.** A duplicate heading with no
  same-or-higher heading after it runs to end-of-file, and bounding it there would take
  `## Project-Specific Notes` with it — so the resolver refused. The `--trust-provenance` escape
  hatch did not work either: `_trailing_span` counted *every* regex match including the fenced
  twin, so it returned `"duplicated"` — the same per-occurrence bug fixed in
  `_unfenced_section_span` and never fixed in its sibling. Both now share `_unfenced_starts`, so
  there is no third copy. The new authorisation is a **proof, not a flag**:
  `_trailing_duplicates_a_deployed_fence` resolves only when the text from the trailing heading to
  EOF equals the body of a fence **already in the same deployed file**, compared against the
  deployed fence and never the incoming render — a drifted live fence still refuses. All six
  measured cases prove; `.claude/agents` goes from 20 refusals to 14. "Trailing" guarantees no
  *heading* follows, not that no *text* follows, so an appended operator note breaks the equality
  and the section is reported instead — tested directly.

### added (`agentteams-updater` — an instance-update expert that cannot write)

- **The agent that carefully updates deployed agentteams instances**, built last and deliberately:
  it depends on `fences._merge_front_matter` and `_insert_section_at_render_position`, both of
  which shipped earlier the same day. It exists for the three judgment classes the deterministic
  merge **refuses on purpose** — capability proposals, both-sides conflicts, and telling a
  project's intentional divergence from stale drift. Anything outside those is `--update --merge`,
  and the agent is instructed to say so.
- **Proposal-only is structural, not instructional.** Its front-matter grant is `['read',
  'search']`, which the Claude adapter maps to `Read, Grep, Glob` — it cannot apply an edit even
  when persuaded it should. This matters precisely because of capability keys: the merge never
  auto-applies `tools:` since widening a grant unattended is privilege escalation, and an agent
  able to apply its own proposals would route straight around that boundary. A prompt-level
  prohibition would not survive a persuasive argument for an exception; an absent tool does.
- **It is never generated into a consumer team.** Gated on an explicit `instance_maintenance`
  capability and never inferred from project text — shipping it everywhere would hand each team an
  agent whose subject is editing that team's own instruction files.
- It refuses three Pre-Flight conditions rather than proceeding carefully: a target not under
  version control, a dirty tree for the files in scope, and an undeterminable generating version.
  Its contract states that a refusal is a successful outcome, that divergence is presumed
  intentional, and that "ambiguous" is a reportable classification.
- The template-ledger ratchet added in this same round caught this template as unregistered on its
  first real use, before the commit. Registered as `TA-045`.

### fixed (the relevance benchmark was ranking one document three times)

- `test_top3_accuracy_perfect` went red on prose added elsewhere in `references/`. The cause was
  not a relevance regression: `references/bridges/<pair>/agent-inventory.md` is the **same
  8,999-byte file in all three bridge directories** (identical sha256), so its three copies tied
  at 8.7782 and filled every top-3 slot, pushing the genuinely correct answer to fourth at
  8.7777. A "top-3 accuracy" benchmark in which one document holds all three slots is measuring
  top-1 with two wasted slots, and a 0.006% margin makes it knife-edge to any corpus change.
- The **benchmark corpus** now deduplicates on content hash. The **product is unchanged and still
  returns all three** — whether `query_index` should collapse identical results for real callers,
  and which path it would then report, is a design question rather than a bug fix, so it is
  logged rather than decided.

### added (two published artifacts now reconcile against the tree)

- **`template-chapter-audit.csv` — a generator was the wrong answer.** The obvious reading of
  "this artifact has no regeneration mechanism" is to write one, but the ledger's contents are
  *judgments* — disposition, severity, chapter relationship — that no code can derive from the
  tree, and `docs_src/template-authoring.md` makes registration a deliberate authoring act. The
  gap was never a missing generator. It was that **nothing checked the authoring rule was
  followed**: `scripts/verify_audit_ledger.py` detects drift but is report-only and manual, and
  CI's four steps do not include it.
  `tests/test_template_ledger_reconciliation.py` closes that half. Measured: 60 templates on
  disk, 32 registered, **28 with no row**. Those 28 are a listed baseline rather than a backfill —
  writing rows for templates nobody audited would fabricate the judgments the ledger exists to
  record. The reverse direction (a row naming a template that no longer exists) is gated at zero,
  because it is already clean.
- **The feature inventory's real defect was the version baseline, not the counts.** The counts are
  hand-maintained for a stated and still-valid reason. The baseline beside them never required
  judgment — it is `agentteams.__version__` — and it read `0.1.0 (2026-04-15)` while the module
  was at `1.0.0rc6`, so a reader checking whether a capability had shipped was reading a document
  describing a different release four months and a major version back. Now pinned, because
  "reconcile it by hand next time" is exactly what produced the gap. The inventory's note is
  corrected to say which three of its numbers are checked and which are not.

### fixed (one source for the Goose capability guidance, actually consumed)

- **The shared source already existed and one of its two consumers ignored it.**
  `agentteams/capability_hints.py` was created so the Goose adapter and the bridge would state the
  research capability identically. `frameworks/goose.py` imported `RESEARCH_CAPABILITY_BULLET`
  and then **never referenced it**, keeping its own hand-written restatement — the exact defect the
  constant exists to prevent, reintroduced under an import that made it look resolved. Nothing
  failed, because nothing checked.
- Worth recording how this was nearly missed a second time: `grep -c 'agentteams.research'` on the
  two modules returns `3` and `0`, which reads as "the bridge is still missing it". The bridge
  reaches the text through the constant, whose body lives in a third file. The measurement was
  wrong, not the code. **The bridged `AGENTS.md` does carry the guidance** — five occurrences.
- The adapter now embeds the shared block verbatim, in two places that had each drifted their own
  way. Goose-specific framing stays local, which is the point of single-sourcing *facts* rather
  than prose: the `computercontroller` renderer contrast and the "search can be added as an MCP
  extension" route are about Goose and belong in the Goose document.
- `tests/test_capability_hint_single_source.py` pins it, including an AST check that neither
  emitter merely imports the constant.
- **`agentteams/frameworks/goose.py` 996 → 834.** The second forced carve of the day: embedding
  the shared text took the module to 1003 and the CH-07 ratchet refused it. The seam was already
  there — `_goosehints_content`, `_resilient_runner_content` and `_goose_capabilities_content`
  build files the adapter *emits*, while the rest is adapter *behaviour*. They moved to
  `frameworks/goose_docs.py` and are re-exported, so no import changed. One test needed updating:
  a `monkeypatch` targeting the re-exported alias would have silently no-opped, so it now patches
  where the name is resolved.
- Two of the four modules in `CEILING_MARGIN_BASELINE` have now been carved by an ordinary edit
  hitting the wall rather than by a decision to decompose. That is the ratchet working, and it is
  also a fair warning about the remaining two.

### fixed (an agent file must not begin with a fence marker)

- **Goose's ACP scanner refused `.claude/agents/team-builder.md`** — `could not find expected ':'
  at line 5 column 1`. The file opened with `<!-- AGENTTEAMS:BEGIN content v=1 -->`, so the
  scanner read the HTML comment as the start of a YAML mapping and hit the template's horizontal
  rule four lines later.
- **It was logged as fence corruption; it was not.** `_normalize_generated_content` already wraps
  *only the body* when a file has YAML front matter, precisely so framework parsers keep seeing
  front matter first. The wrapper lands on line 1 for one reason: there was no front matter for it
  to land after. **The cause was a single outlier template** — three of the four builder templates
  open with a `---` block, the Claude one did not, and `render_builder_file` is identity for
  Claude, so nothing downstream injected it the way `render_agent_file` does for ordinary agents.
- The template now carries front matter, and this repo's deployed file was repaired directly.
  That second step was necessary rather than redundant: **`--update --merge` cannot add front
  matter to a file that has none**, because front matter lies outside every fence and is preserved
  verbatim. Verified by dry-run before and after — the merge reported `+1 bytes` on the broken
  file and `+235 bytes` once the block existed for it to preserve.
- `SETUP-REQUIRED.md` also opens with the wrapper and is **deliberately left alone**: it is a build
  report, not a persona, and both `emit._is_agent_doc` and `bridge_sources` exclude it from
  agent-file enumeration. Nothing scans it.
- Guarded by `tests/test_agent_file_front_matter_first.py`, which asserts the property per builder
  template rather than for the one file that broke.

### fixed (`.vscode/tasks.json` can no longer be written outside the output tree)

- **`vscode_tasks_rel_path` returns a fixed `../../.vscode/tasks.json`**, correct for every adapter
  that overrides it because each puts its agents dir exactly two segments below the project root —
  and wrong for any other `--output`. Pointed at `examples/<name>/expected`, one segment below its
  own conceptual root, the offset climbed a level too far and wrote `examples/.vscode/tasks.json`,
  a sibling of every example project. It went unnoticed because the snapshot comparison reads only
  `*.md`/`*.svg` inside `expected/`.
- The call site now **derives** the expected depth from each adapter's own `get_agents_dir`
  contract rather than restating "two levels" a second time, and **refuses with a message** when
  `--output` is not shaped like that adapter's agents dir. Refusing beats guessing a corrected
  offset: an arbitrary `--output` has no discoverable project root, so any inferred target is a
  path written somewhere the operator did not name. No adapter contract changed and no existing
  assertion moved.

### changed (three more template-owned sections fenced — and one deliberately not)

- `conflict-auditor` and `navigator` gain fenced `invariant_core` sections; `orchestrator` gains
  `update_compatibility_source_pack`. Partially-fenced templates now carry 638 lines outside a
  fence, down from 669.
- **The orchestrator's own `## Invariant Core` was left unfenced on purpose, and the reason is
  recorded in the template.** Its rule list carries the ⛔ "Do not modify or omit" banner while the
  SECTION MANIFEST designates `constitutional_rules` USER-EDITABLE — and projects *do* extend it;
  this repository's `CLAUDE.md` adds rules past the template's set. A fence spanning the heading
  through the last rule would replace that list wholesale on `--update --merge` and delete every
  project-added rule. The banner and the designation genuinely conflict; the designation wins,
  because it is the one whose failure mode is data loss.

### added (plan-step CSVs are checked against the real corpus)

- **The CSV-safety instruction existed in two agent files and did not prevent recurrence.**
  "Write with `csv.writer`, re-parse via `read_steps`" has been in both orchestrator files since
  2026-07-22; two days later this repo produced several rounds of the exact corruption it forbids.
  `tests/test_plan_steps_corpus.py` now points the existing detector at the files that actually
  accumulate. An instruction that competes with finishing the work loses; a test does not.

  Two design points worth stating, because both cut against the obvious implementation:
  - **It skips, rather than passes, on an empty corpus.** `tmp/` is gitignored and carries zero
    tracked files, so this glob matches nothing in CI. A green result on an empty corpus would
    read as coverage while checking nothing.
  - **It is a ratchet, not a zero-gate.** 13 of 187 files already overflow, the oldest from
    2026-W19. They are records of finished work; rewriting them to make a test pass would be
    editing the record. The baseline lists them by path, so repairing one means deleting a line
    and the debt stays legible.

### changed (`devDependencies` no longer spawn a reference document each)

- **`_parse_package_json` categorised every entry in both `dependencies` and `devDependencies` as
  `library`**, which routes to the reference tier and gives each one its own
  `references/ref-<tool>-reference.md`. A mid-sized JavaScript project carries dozens of lint
  plugins and type stubs, and each was producing a document. `devDependencies` are now categorised
  `other` — passive by default — while staying in `tools[]`, so stack inference is unaffected.
  `other` rather than a new `dev-tool` value because `category` is enum-constrained in
  `schemas/project-description.schema.json`, which outranks the pipeline.
- **This is a default, not a filter**, and the distinction is what makes it safe: name-based
  promotion in `classify_tool_importance` still fires, which is where the tools that matter live.
  `typescript`, `webpack`, `vite`, `rollup` and `esbuild` were added to `_REFERENCE_TOOLS` in the
  same change — without them the demotion would have silently taken a TypeScript project's
  compiler and its bundler from reference tier to passive. They are listed as *reference*, not
  specialist, so they keep exactly the tier they had. Test frameworks (`jest`, `pytest`, `mocha`,
  `junit`) were already there for the same reason.

### changed (orphan advisories say what `--prune` cannot do)

- **The older `*.agent.md` orphan advisory said "Review and delete if obsolete"** while its
  newer sibling for `ref-*-reference.md` files already disclosed that `--prune` cannot reach
  them. `--prune` deletes only what the build-log diff records as removed; both advisories find
  their orphans by globbing the output directory, which `--prune` never consults. The wording is
  now identical in both.
- **Wiring the glob results into `--prune`'s deletion set was declined**, not overlooked. That
  would widen a destructive flag's reach from an authoritative build-log diff to a heuristic
  glob — a decision that belongs to `@security` under Constitutional Rule 1, not to a
  consistency fix.
- **`agentteams/cli/generate.py` 991 → 958.** This carve was forced, not chosen: a six-line
  comment took the module to 998 and the CH-07 ratchet refused it, which is the guard working
  as designed and the exact failure mode recorded for that module in the remediation log. The
  carve it forced was the right one anyway — the inlined orphan-agent advisory moved to
  `build_team._report_orphan_agent_files`, next to the reference-doc advisory it mirrors. The
  two had been describing the same blind spot from two different files. `cli/generate.py` leaves
  `CEILING_MARGIN_BASELINE`; three modules remain.

### changed (three governance documents corrected)

- **The retrospective's self-referential exception cited the wrong reason.** It justified routing
  remediation rows to the top-level CSV by calling the dogfood tree "gitignored", naming only
  `.github/agents/`. There are two dogfood trees, and `.claude/agents/` *is* git-tracked — Claude
  Code needs it resolvable on disk. The property that actually matters is that both are
  **regenerable**, so a row appended to either is overwritten by the next build. The clause now
  says that, and warns against re-narrowing it to the gitignored tree.
- **AUTHORING-GUIDE §6 gains the `extra_output_files` sub-pattern.** These generators take no
  manifest parameter, so they cannot see team composition. Content depending on it needs
  verify-first phrasing ("check whether the team includes X"), not an unconditional assertion and
  not a signature change threading manifest access through for a sentence of prose. Caught twice
  inside `goose.py`, both times in audit rather than authoring.
- **The work-summarizer's `append` mode gains a required idempotency rule.** The documented
  once-per-session guard is scoped to Workflow D's backfill sweep; the append path that
  completion-capture actually uses had no guard at all. One daily file accumulated twenty
  `Session Stop` / `Workflow D` headings across 1,594 lines with "No Gaps Detected" repeated
  verbatim three times. The rule bounds the decision on **evidence**, not on session identity —
  a session is not observable in the file, and completion-capture fires several times within one.
- **Filing conventions gain a scope-drift section.** A plan whose exit criterion is a live
  end-to-end run will discover defects in modules it declared out of scope, and its Non-goals
  then become false mid-flight. The convention: amend the Non-goal explicitly, record the scope
  change as its own numbered step rather than renumbering to `10b`/`10c`, and notify
  `@repo-liaison` when the newly-touched module is shared. It applies only where the declared
  exit criterion is unreachable without the out-of-scope fix.

### changed (remediation log: three rows closed by verification)

- **Two rows described defects that no longer exist**, and one describes something outside this
  repo. Rating and sequencing work kept inheriting them, so they are closed with cited evidence
  rather than carried into another round.
  - The Goose preflight's blindness to the newer `providers:`/`active_provider:` config schema:
    `scripts/goose-openrouter-preflight.py` already has `parse_goose_providers_block`, and a live
    run resolved `provider: ollama` from `active_provider` instead of the empty
    "nothing to validate" the row predicts. → `shipped`
  - The auto-fence retrofit's missing file-type guard: `_is_machine_managed_merge_overwrite_path`
    full-replaces `.py`/`.json`/`.svg` and fences only `.md`, so no non-Markdown file can be
    auto-fenced. No data file in the repo carries a fence marker. → `shipped`
  - OpenRouter upstream tool-calling variance is provider-side — the same model id served by
    different upstreams differs in fidelity, and nothing here can change a third party's backend.
    The measurement is retained as the reason. → `wontfix`

  Backlog: 24 open → **21 open / 37 shipped / 2 wontfix**. Both `shipped` closures were checked
  against test-pinned code (`tests/test_goose_openrouter_preflight.py`, `tests/test_frameworks.py`),
  not read off the source alone.

### added (security policy: three gaps closed under one review)

- **The Mandatory Review Triggers table gains rows for package installation and elevated
  privilege.** That table is what mechanically decides when `@security` must review before an
  action proceeds, and it had no row for `brew`/`apt`/`pip install` or for `sudo` — only Rule
  S-4's general destructive-operation prose, which is weaker than a table row. The gap became
  live when the CLI-tool-discovery reference started telling agents they may install missing
  tools. The fence is bumped `v3 → v4`, so the change reaches deployed teams.
- **Rule S-9's metered-endpoint edge is named.** A read-only GET against an API that bills per
  call is neither clearly "privileged or stateful" nor clearly a free inert public fetch. It is
  now explicitly *not* a criterion-5 match — no credential or data exposure — but carries a
  **disclosure** obligation: say the endpoint is metered and roughly what a run costs. Cost
  control stays outside S-9's purpose, which is exposure; naming the boundary stops it being
  resolved silently in either direction.
- **`scan.py` now implements S-5's scan-derivable subset.** It had declined instruction-override
  detection wholesale as "not scan-derivable". That was true of S-5's *third* bullet — a heading
  that redefines agent identity needs judgment — but bullets one and two are literal strings, and
  declining them left a static check undone that the template says must happen before any
  verdict. It matters here specifically: project-supplied text is rendered into agent files a
  model later reads as instruction.

  Exempt inside YAML front matter (`description: "you are now the reviewer"` describes a role
  rather than overriding one) and inside code spans (S-5's own rule text quotes every pattern in
  backticks) — the latter reusing the existing `_match_inside_code_span` helper rather than
  special-casing a file.

### fixed (a test that pinned a version instead of ratcheting it)

- `test_fence_version_bumped_to_3` asserted the security invariant's fence was *exactly* `v=3`,
  so it failed on the next legitimate change rather than on a regression — which is what happened
  when the trigger table gained its two rows. It now asserts the version never goes backwards,
  which is the property the version actually encodes.

### added (42 template-owned sections fenced — and the scope was wrong twice)

Example teams go **76% → 81% updatable**. Getting there corrected the premise twice, and both
corrections matter more than the fencing.

**A wholly-unfenced template already updates completely.** Its entire body sits inside the
`content` wrapper, and merge *replaces* fenced regions — verified directly:
`sections_replaced == ['content']`. Fencing those 32 templates would have added 145 fences for
zero gain. Worse, it would have been a **regression**: converting a file from wholly-wrapped to
partially-fenced strands everything that did not get an explicit fence. This retroactively
justifies reverting 19 templates earlier, for a better reason than the one given at the time.

**So the stranded content lives only in *partially*-fenced files** — 17 of 34 deployed agent
files, 798 lines. Those are what this fences: 42 sections across 19 templates, selected by the
same three-part test (no project-specific placeholder, not under a USER-EDITABLE heading,
template-owned), with the boundary stopping at the next heading **or** the next fence marker.

### fixed (the blocker behind ~19 unfenceable templates — and the logged reason was wrong)

Lane 2's pilot. Its job was to find out whether the 19 templates with no fences could be reached
at all. They can, and the recorded reason they could not was mistaken.

**What was logged:** "adding an inner fence produces `Nested fence not allowed: 'invariant_core'
inside 'content'`." **What is actually true:** adding a fence *suppresses* the whole-body wrapper,
so nesting never occurs. That error came from a boundary bug in the fencing pass — a section's END
marker landing inside the following fence — which was fixed separately.

**The real blocker is duplication.** A template with no fences has its whole body wrapped in one
`content` fence at emit. When it gains a named section the render stops being wrapped, so a team
generated *before* the split has `{content}` on disk while the render has `{invariant_core, …}`.
Merging appended the named section *alongside* the stale wrapper — leaving an agent file with two
contradictory copies of its "⛔ Do not modify or omit" contract. Worse than not updating.

`_merge_fenced_content` now detects that shape and replaces wholesale. Safe **because of what a
fence means**: everything inside `content` is template-owned and already overwritten on every
merge, so nothing a project authored can live there. Content outside it is untouched, and the
migration is reported rather than silent.

This unblocks the remaining fencing work; the 19 templates are now fenceable.

### changed (verification sweep: the backlog shrank by checking, not fixing)

Lane 1 of the cluster sequencing — verify every unverified row, close what is already done, and
fix only what survives.

- **`build_tool_catalog(fetch_pypi=...)` → `fetch_registries`.** The npm tier was added under the
  PyPI-era flag without renaming it, so a caller passing `fetch_pypi=False` to avoid PyPI was also
  silently disabling npm. The old name is kept as a documented deprecated alias, so the existing
  call site and tests are unaffected.
- **Backlog 33 → 28 open, 1 wontfix.** `--refresh-index` was already the narrow reindex path #20
  asked for (verified: it writes only the index and its `.vcache`, no team files). Row 15's
  misattribution can no longer misdirect anyone now that row 15 is closed. The Goose provider docs
  already carry the correction they were logged for — the grep that seemed to reopen that row was
  matching the *correction text*. And `GeneralResearchTeam` is a parent folder holding research
  repositories, not a repository, so being unversioned is correct.
- Three rows re-verified as genuinely open and annotated with what was found, so the next pass
  does not repeat the check.

### fixed (the closure guard assumed every closure points at code)

- A `wontfix` is closed by a **decision**, not an artifact — "the operator confirmed this is not a
  defect" is the only evidence such a row can have. The evidence-anchor check rejected it and
  would have pushed authors to invent a file path. Anchors widened and made case-insensitive.

### added (the fencing the positional-insertion fix unblocked)

- **`Invariant Core` is now fenced in 9 templates**, so it can finally reach an already-generated
  team. It is the clearest template-owned section in the library — it literally reads "⛔ Do not
  modify or omit" — and it sat outside every fence.

  Selection was by explicit test, not by eye: no `{PLACEHOLDER}` rendering project-specific data,
  not under a `USER-EDITABLE` heading, and **the file must already have a fence**.

  That last condition is why this batch is 9 and not 25, and it was learned on contact. A template
  with no existing fence gets wrapped whole in a `content` fence by `_normalize_generated_content`,
  so adding an inner fence yields *"Nested fence not allowed: 'invariant_core' inside 'content'"*.
  Nineteen templates were reverted for it; reaching them needs a different mechanism and is logged.

  A second constraint surfaced the same way: taking a section's boundary as "up to the next `##`
  heading" put the END marker *inside* the following fence, because a fenced section's heading sits
  after its BEGIN marker. It showed up as an existing regression test going red —
  `test_external_retrieval_quality_gate_reaches_already_generated_team`, which exists to protect
  exactly this propagation path.

### fixed (a gate that modelled one of CI's four steps)

- **`agentteams.1` regenerated for `--allow-foreign-output`.** The pre-push gate was "full suite
  green", and the suite *was* green at 2,937 — but CI also checks man-page currency and runs the
  RSR1 durable-artifact lint. A gate modelled on one of four CI steps is not a gate on CI. Both
  non-pytest steps are now verified locally before pushing.

### changed (backlog reconciliation)

- Four more rows closed with evidence, including the two mechanisms shipped this session
  (positional fence insertion, front-matter propagation). The five-crowded-modules row is
  superseded by a narrower one for the four that remain after `cli/artifacts.py` was decomposed.
  Open rows: 48 → 33 across the session.

### added (existing agent files are now genuinely updatable)

Plan and audit: `tmp/by-week/2026-W31/agent-file-updatability.plan.md`.

Measuring first corrected the premise. Across this repo's 34 generated agent files, **76% of body
content already updated** — 2,657 fenced lines against 723 template-owned-but-unfenced and 770 of
front matter. Only 121 unfenced lines are legitimately the project's. So "template changes never
reach deployed teams" was too pessimistic; the real gaps were two specific mechanisms.

- **Positional fence insertion — the keystone.** A section present in the fresh render but absent
  on disk was appended at the *absolute end of the file*, whatever its position in the render. A
  template author adding a gate step meant to run **before** an existing instruction got correct
  placement on a fresh build and a silently inverted execution order on `--update --merge`. It
  also made retrofitting fences impossible, which is what stranded those 723 lines:
  `fence_inject` no-ops on any already-fenced file, so a template gaining a fence was the only
  route into a deployed team, and that route mislanded. New sections now anchor to the nearest
  preceding section, then the nearest following one, and only append as a genuine last resort —
  which is now *reported* rather than silent. Existing content is never reordered; a file whose
  author deliberately arranged its sections keeps that arrangement.
- **Three-way front-matter merge.** Front matter can never be fenced — YAML must be the literal
  first bytes, before any HTML comment — so it needed a different mechanism, not a better fence.
  The build log now records the front matter *as emitted*, which makes "the project never touched
  this key" provable rather than assumed. That is precisely what was missing when
  `--sync-front-matter` was withdrawn a day earlier: a two-way difference cannot tell you who
  caused it. Template moved and the project didn't ⇒ apply. Both moved ⇒ conflict, keep theirs,
  report. No baseline ⇒ apply nothing, because an unknown baseline is not permission.
- **Capability keys are still never applied automatically.** `tools`, `model` and `agents` are
  reported as proposals however clean their provenance. Proving nobody edited `tools:` is not the
  same as having authority to grant a downstream agent shell access — the security carve-out from
  the plan audit, and the reason this is safe to enable by default.

End to end on a synthetic deployed team: a metadata change propagates, a `tools:` change is held
back with the exact line to apply. That is the `retrieval`-grant case that started this.

**The pilot measured rather than assumed.** Fencing one section (`Invariant Core` in
`tool-doc-researcher`) touches 3 files, 3 added lines each, no collateral — so completing the
fencing is mechanical and low-risk per section, just repetitive. It is logged as open with that
number attached, so the decision to continue rests on a measurement.

The update *agent* is designed and deliberately deferred to its own round: it proposes and never
writes, lives in agentteams rather than being generated into consumers (an agent editing its own
instruction file mid-run is a footgun), and refuses targets not under version control. Building
the highest-privilege component before its substrate is trusted is the wrong order.

### fixed (the backlog was 27% stale again, one day after shipping the fix for that)

Tier 1 of a tiered backlog remediation. Plan, per-item audits and tier meta plans:
`tmp/by-week/2026-W31/backlog-tiered-remediation.plan.md`.

- **13 rows closed with evidence; 48 open → 34.** Every one was verified against the tree, and
  each closure names a test, module, commit or stated verification a later reader can check. The
  condition is embarrassing and worth stating plainly: closure columns were added on 2026-07-30
  *because* rows were fixed-but-open, and the three rounds that followed fixed a dozen more
  items without closing a single row — because nothing in the process invoked the new columns.
- **`tests/test_session_closeout_obligation.py` — the obligation as a test, not prose.** A
  convention someone has to remember loses to finishing the work; that is the whole lesson here.
  It checks that closures are evidenced, that dates are coherent, and that the reconciled share
  of the log does not collapse back toward zero. It deliberately does **not** guess which rows
  *should* close — an automated guess that closes a live row is worse than a stale open one, and
  that design was proposed for this round and rejected in audit. `references/filing-conventions.md`
  states the rule so a close-out failure is not someone's first notice of it.
- **`cli/artifacts.py` decomposed, 976 → 621.** The code-index half — a *gitignored, rebuildable
  cache*, as against the memory index's *committed* artifact — moved to
  `cli/code_index_artifacts.py`, with re-exports so every import site resolves unchanged. The
  split was already latent: a section rule separated the two clusters. It leaves the crowded set,
  and the currency test now enforces that the baseline says so.

**Audit changed the scope three times, and that is the point of running one.** Starting with
`audit.py` was rejected — at one line of headroom it is the *worst* place to learn what a
decomposition costs, not the safest, so the roomiest module went first. An automated
staleness-detector for the log was rejected as the original defect in a new costume. And the
fleet-wide front-matter propagation item was removed from Tier 1 entirely: it writes privilege
declarations across repositories, one of which has no version control, so bundling it beside a
log reconciliation was a scoping error rather than an ambitious plan.

Four modules remain crowded (`audit.py` 999, `goose.py` 996, `graph.py` 992, `generate.py` 991),
held by the ratchet and logged. Tiers 2–4 are planned but explicitly **provisional**: 32 of their
rows are unverified, and every enumeration this session has found the log stale, so each of those
plans begins with verification rather than trusting its own description.

### fixed (round close-out — and one deferral whose stated reason was simply wrong)

Closing the items the medium-complexity round left open. The audit lens set was widened this
time — `@technical-validator`, `@code-hygiene`, `@security`, `@repo-liaison`, `@test-suite-expert`
alongside the usual two — after several rounds in which the narrow pair kept missing the same
class of error: a claim about the tree that nobody checked. It caught one on first use.

- **Unfenced prose drift is now reported, and the reason it was deferred was false.** The
  previous round deferred it because "an edit cannot be told apart from intended authorship
  without a provenance mechanism the format does not have". `references/build-log.json` records
  a per-file hash of what was last emitted (45 entries), and
  `drift.detect_user_customizations` already compares it. The mechanism existed; it went
  unchecked. Drift in unfenced regions is now reported for files whose hash still matches — i.e.
  files nobody has edited, where divergence is template drift by elimination. For a modified
  file it stays silent, which is correct: that prose is the operator's. Built on the existing
  detector and the existing comparison, not a parallel implementation.
- **The drift notice now carries the exact line to change.** An auto-applying
  `--sync-front-matter` was designed and **withdrawn** in audit: `tools:` is a privilege
  declaration, so writing it unattended is a privilege-escalation path, and the same command runs
  against consumer repos — one of which has no version control, making a bad write
  unrecoverable. A human applying a one-line edit they can see is the right cost. The fleet-wide
  propagation gap stays open and logged, visibly, rather than half-fixed.
- **The nondeterministic live test no longer reddens ordinary runs.** Measured over five runs:
  two passes, three failures, failing identically with every local change stashed. It asserts
  that a live model emits `delegated` rather than `early-stop` — and this repo's own log already
  records that OpenRouter backends differ in tool-calling correctness, which is why
  `scripts/goose-run-resilient.py` exists. It measures the provider, not this code. Now gated on
  `AGENTTEAMS_LIVE_MODEL_TESTS=1`, with a skip reason stating the pass rate and why. The stable
  live probe is untouched. Side effect: a suite run is ~100s faster.
- **A CH-07 headroom ratchet, and the ceiling problem is bigger than one module.** Adding an
  early-warning guard revealed **five** modules within 25 lines of the 1000-line ceiling —
  `audit.py` at 999, `frameworks/goose.py` 996, `graph.py` 992, `cli/generate.py` 991,
  `cli/artifacts.py` 976. Three have under ten lines of runway, which is why three unrelated
  carves landed inside one session: nobody gets a warning until they are already blocked. The
  planned fourth carve was **withdrawn** — relocating gate-adjacent control flow to buy line count
  trades real risk for a number, against a docstring calling the pipeline's linear order
  load-bearing. Instead the crowded set is recorded as a ratchet (the `BROAD_EXCEPT_BASELINE`
  precedent): a listed module may shrink, never grow, and a new entrant fails. A third test keeps
  the baseline honest by failing if it lists a module that has since been decomposed.
- **`references/freshness-gate-scoping-decision.md`** — the security-gate scoping question, laid
  out with the argument on each side and **no recommendation**. How much security margin to trade
  for convenience is the operator's call, and the sharpest fact belongs in front of them: the
  status quo's observed effect was an operator bypassing the CLI entirely, which skips every gate
  rather than one.

**A third measurement fell out of the close-out.** Adding one unrelated reference document to
the corpus moved paraphrase top-3 recall from 3/10 to 2/10 — isolated by removing the file and
re-measuring — while keyword recall stayed at 10/10. The floor was lowered to the observed value
rather than the eval being adjusted to protect the old number, which would be fitting the
benchmark to a preferred answer. It is a second, independent measurement of the same weakness:
lexical paraphrase matching is fragile enough that an unrelated addition displaces a correct
answer, and the corpus only grows.

Three items are logged as deliberately open: the five-module decomposition, fleet-wide
front-matter propagation, and scheduling the live provider check so regressions are noticed
without gating.

### fixed (merge preserved your edits and never said what it had skipped)

Remediation of every **Medium-complexity P1/P2** item. Plan, audits and clustering:
`tmp/by-week/2026-W31/medium-complexity-p1p2-remediation.plan.md`. Verification-before-planning
again changed the scope: one item was already fixed, two were one defect, and one new defect was
found in the previous session's own work.

- **`--update --merge` now reports front-matter drift.** Agent-file YAML front matter lies
  outside every `AGENTTEAMS` fence, so merge preserves it verbatim — correct, and how a project
  keeps its own edits, but silent. Adding `retrieval` to two templates reached no
  already-generated team and the run said nothing; both files needed hand-editing. Driving
  `_merge_fenced_content` directly: fenced body propagates, front matter and unfenced prose do
  not, and `shrink_notices` is empty. The fix is **detection, not different semantics** —
  applying the template's front matter would overwrite user-owned values, the precise failure
  merge mode exists to prevent. `MergeResult.front_matter_drift` reports drifted *keys*
  (never values-as-diff), surfaced through the existing notice channel. `name`/`description`
  are exempt: they interpolate the project name and would otherwise fire on every file in every
  run, and a notice that fires on everything gets muted. Unfenced *prose* drift is explicitly
  **not** reported — without a provenance mechanism the format lacks, an edit cannot be told
  apart from intended authorship.
- **`--output` write-target guard (`cli/output_target.py`).** A scratch render with a relative
  `--output .claude/agents` once resolved against an unexpected working directory and overwrote
  this repo's real agent tree; it was recovered from the tool's own backup, and the CLI had
  raised nothing. The guard **fails open by design**: it refuses only a relative path landing on
  a non-empty, git-tracked directory with *no* sign of ever being agentteams-generated. Updating
  a real team is never blocked; anything unclassifiable warns and proceeds. `--allow-foreign-output`
  overrides. The tests put the legitimate-update cases first, because a guard that refuses
  `--output .github/agents` would be worse than the defect.
- **CH-05: one name for the backup directory.** `.agentteams-backups` was restated as a literal
  in **ten** places across nine modules (the log recorded four). Now a single
  `backup.BACKUP_DIR_NAME`, imported everywhere; grep confirms zero remaining literals outside
  the definition. Only the *name* is shared — `architecture._EXCLUDE_DIRS`,
  `interop._NON_AGENT_DIRS` and `artifacts._SCRATCH_DIR_NAMES` keep their own membership,
  because pruning an import-graph walk, filtering agent discovery, and bounding index sources
  are three different questions and collapsing them would be a worse coupling than the
  duplication it removed.

### fixed (two defects in the previous session's own CLI work)

- **`--dry-run --json` was still broken on the `--self` path.** Last session's fix wrapped only
  `run_generate`, leaving every print issued *before* dispatch on real stdout: `cli/app.py`'s
  "Self-maintenance mode:" banner landed ahead of the document and `json.load(sys.stdin)` failed
  at line 1 again (stdout 12 897 bytes, banner on line 1, JSON from line 2). Silencing that one
  line would have fixed the symptom and left the next added print to re-break it — the boundary
  was the bug, so it moved out to `main`. The wrapper is now re-entrant, because `main` and
  `run_generate` both wrap and a naive second entry would stash the already-redirected stream.
- **The security-gate codes named the wrong gates.** Introduced hours earlier to make two
  indistinguishable gates distinguishable, they were action-first and filed the *freshness* gate
  under `[SEC-GATE/WRITE-PATH]`. Three sites raise from `_assert_destructive_action_allowed` and
  one from `_assert_security_intelligence_fresh`; the codes are now gate-first
  (`DESTRUCTIVE:<action>` / `INTEL-FRESHNESS`), and a test walks back from each message to the
  `_assert_*` call above it and fails if the code names the wrong gate.

### changed (a security gate diagnosed, deliberately not weakened)

- The stale-intelligence gate blocks an entire run for all files, and the logged workaround was
  to bypass the CLI and drive ingest/analyze/render/emit by hand — which skips *every* gate. The
  planned remediation was to scope the gate; the audit rejected that as **weakening a security
  control on convenience grounds**: a team whose security agent quotes expired advisories is a
  hazard regardless of which file is written this minute. Scoping it is an operator decision, not
  mine. What ships is the diagnostic half — the refusal now states its blast radius, naming how
  many intel-bearing placeholders would actually be interpolated, so the operator can tell "the
  intel is load-bearing here" from "one reference file is held up by a cache timestamp".
  Recorded as a deliberate scope reduction, not a completed remediation.

### fixed (the backlog said 53 open items; 8 were already done and 2 more were misdescribed)

Remediation of every **Low-complexity P1/P2** item in the 2026-07-30 open-items enumeration,
grouped into five clusters. Plan, audits and clustering:
`tmp/by-week/2026-W31/low-complexity-p1p2-remediation.plan.md`.

Verifying each item against the tree before planning changed the work three times, which is the
main lesson of the pass:

- **The bridge-manifest absolute-`source_dir` item was already fixed.** `bridge.py` has a shared
  `rel_to_root()` and all three manifests record the relative `.github/agents`. Dropped.
- **The memory-index item was misdescribed.** Document *paths* were already relative. What
  actually leaked was indexed *snippet content*. And the "276 of 632 indexed documents are
  gitignored" figure — while true — is **not a defect**: `_memory_index_sources`'s docstring
  already rejects gitignore-based exclusion, correctly, because `workSummaries/` and
  `references/plans/` are gitignored yet *are* the durable history the index exists to serve.
  Gitignore marks "local", not "disposable".
- **The stale top-level `/templates/` was gitignored and untracked**, so no clone ever carried
  it. The hazard was real but local-only; the durable fix is the guard, not the deletion.

**M1 — the remediation log could not express "done".** All 53 rows read `open`, including 8
verifiably fixed, so the log overstated outstanding work by ~15% and prioritising from it wasted
effort on solved problems. Added `resolved_date` + `resolved_evidence`; closed only rows with
*mechanical* evidence (a named test, file or workflow that demonstrably exists), recording that
evidence per row. The existing `tests/test_remediation_log_shape.py` was extended (rather than
duplicated — it already owned this file) to assert the documented lifecycle vocabulary, that a
terminal row carries both date and evidence, and that a non-terminal row carries neither.

Two governance details the plan audit missed and the test suite caught: the documented lifecycle
is `open → triaged → shipped | wontfix`, so the `resolved` status this change first invented was
wrong and the rows now read `shipped`; and `liaison_logs.AGENTTEAMS_REMEDIATION_HEADERS` — not the
CSV — is the header's single source of truth, so the schema change belonged there.

Three further fixes fell out of running the suite, each an existing guard catching this work:

- The repo-wide absolute-home-path guard allowlisted the memory index on the grounds that its
  snippets are "verbatim excerpts". That reasoning does not survive inspection — snippets are
  already truncated to 480 chars, newline-collapsed and heading-stripped, and `source_hash`
  attests to the source document, not the excerpt. The allowlist is now **empty**, with the
  reversal recorded where the old justification lived.
- `cli/generate.py` stood at 998 lines against the 1000-line CH-07 ceiling, so any addition broke
  it. Rather than weaken a ratchet that had zero exemptions, two coherent units were carved out:
  `cli/json_mode.py` (the `--json` stdout discipline) and `cli/exit_codes.py`
  (`_finalize_exit_code`, re-exported so existing imports are unaffected). 994 lines.
- `docs_src/api-reference/emit.md` was updated for `print_dry_run_report`'s new `stream`
  parameter, caught by the signature-parity test added earlier this session.


**M2 — committed artifacts carried machine-local state.** The tracked `.claude` memory index held
49 absolute `/Users/…` strings inside snippet text, some naming an unrelated repository — and
because the source documents are frequently local-only, the index was the *only* place those
strings were committed. `memory_index.redact_local_paths` now redacts at snippet-construction
time (both the paragraph path and the legacy `_snippet` path, since either would reopen it).
Regenerated: 0 hits. Retrieval was re-measured before and after — keyword 10/10 and paraphrase
1/10 both unchanged, so the hygiene fix cost nothing. Separately, generated teams now get the
`.vcache`/`code-index` ignore rules as an **operator action in SETUP-REQUIRED.md** rather than
agentteams editing a consumer's `.gitignore`, which is not its file to write.

**M3 — three ways the CLI told the operator the wrong thing.** `--dry-run --json` promised "a
single JSON document on stdout" and emitted nine progress lines plus one `[DRY RUN] WRITE` line
per file ahead of it, so `json.load(stdin)` failed at line 1; JSON mode now redirects human
output to stderr and hands the real stdout to the report writer. (Patching one of the two
`print_dry_run_report` call sites was not enough — the generate path silently kept writing JSON
to stderr, and a test now pins that every call site passes the stream.) A no-op update no longer
claims "no changes detected" before rewriting every live-data fence. And four gate failures
across **two different gates** printed near-identical messages; each now carries a distinct
`[SEC-GATE/…]` code. Two existing tests asserted on the old strings — one with a comment
explaining how it had to pick a prefix precise enough to exclude the *other* gate's message,
which was itself evidence of the defect.

**M4 — conventions with nothing enforcing them.** `FENCE-CONVENTIONS.md` requires a SECTION
MANIFEST in every fenced template; 10 of 26 had none and 4 more were incomplete. The guard was
written and run against the unfixed tree first (14 failures, recorded) before anything was
repaired, and it asserts the manifest *matches the file's real fence IDs* rather than merely
existing — a manifest that misdescribes a file is worse than none, because it is believed.
Designations are derived, not judged: an AGENTTEAMS-fenced region is by definition replaced on
`--merge`, so it is `FENCED`. Also adds `scripts/regen_example_snapshots.py`, which refuses to
run on a tree dirty outside `examples/` (regenerating over unrelated changes bakes them into the
goldens) and never adds a snapshot that does not already exist.

**M5 — stale references to a layout that changed.** Removed the stray top-level `templates/`
after an evidence pass (3 files, 0 tracked, gitignored, zero readers; every `TEMPLATES_DIR`
resolves to `agentteams/templates`; the stray carried 9 constitutional rules against the
canonical 28 and nothing unique). `tests/test_canonical_template_root.py` is the lasting part:
it pins that no module binds `TEMPLATES_DIR` to the repo root, tolerates the several legitimate
spellings of the canonical path, and proves its own predicate can fire. Corrected nine citations
in `.claude/CLAUDE.md` that still named `src/` (renamed to `agentteams/`) and the removed
top-level `templates/`.

### fixed (schema drift that made strict manifest validation fail, and the missing test behind it)

- **Three separate drifts between `build_manifest` and `team-manifest.schema.json`**, all
  pre-existing, all found by one new test. The schema sets `additionalProperties: false`, so a
  real manifest failed strict validation: four undeclared top-level fields
  (`existing_project_path`, `governance_agents`, `code_index_extra_dirs`,
  `memory_index_extra_dirs`), `graph-svg` missing from the `output_files[].type` enum, and
  `research-analyst` missing from the `selected_archetypes` enum. Types were read off real
  emitted values rather than inferred from names.
- **`tests/test_manifest_schema_conformance.py` — the test that would have caught all three.**
  Every existing schema test validates a hand-written *fixture*, which by construction contains
  only fields someone remembered to include, so the gap between what the schema declares and what
  the generator emits was structurally invisible. This validates `build_manifest`'s own output
  across seven descriptions and all four frameworks. It deliberately does **not** assert
  declared == emitted in the other direction: six declared fields (`mcp_*`, `recipe_*`,
  `adopted_agents`) are correctly conditional, and an equality assertion would "fix" them by
  deleting live schema.

### added (a charter an agent cannot fulfil is now a test failure)

- **`tests/test_agent_charter_tool_parity.py`.** `tool-doc-researcher`'s description promised it
  "locates and verifies official documentation"; its tools were `['read','search']`, where
  `search` is Grep/Glob. It could not fetch documentation, and neither could `reference-manager`
  with its "citation verification" charter — for the life of the project. Nothing compared the
  two, though they are authored four lines apart in the same file. The check reads the
  `description:` line only, fires on explicit external indicators, and is suppressed by locality
  qualifiers so `technical-validator` ("match what exists **on disk**") stays correctly clean.
  It includes proof-of-failure cases, and does **not** auto-grant anything: which agents hold
  `retrieval` is a least-privilege decision recorded in `references/retrieval-transport-policy.md`.

### changed (the dense-retrieval deferral now rests on a measurement, not a guess)

- **`tests/test_memory_index_paraphrase_recall.py`.** The 2026-07-30 review deferred a dense tier
  "until there is evidence lexical scoring is the binding constraint" — with no evidence either
  way and no way to obtain any. Pre-registered construction rule and hypothesis, then measured on
  the same corpus, the same ten target documents, and the same BM25 retriever, varying only the
  wording:

  | Query style | top-1 | top-3 |
  |---|---|---|
  | Keyword (document's own vocabulary) | **10/10** | 10/10 |
  | Paraphrase (deliberately avoiding it) | **1/10** | 3/10 |

  Nine of ten paraphrased queries never surface their target at all. The lone top-1 hit is not a
  real exception — "command line" is `cli-reference.md`'s own phrasing. This does not make a dense
  tier automatically correct (BM25 is excellent when the caller knows the words, and a dense tier
  still needs a dependency the stdlib-only base forbids); it converts an open-ended deferral into
  a decision with a measured cost and a benchmark any replacement must beat. The test asserts
  *floors* at the measured values so a regression fails but the known limitation does not.
  Recorded in the report's §5 Tier 3.

### added (the unverified scoped-`Bash` grant is now tracked, not just disclosed)

- **`framework_research._scan_scoped_tool_support`.** Whether Claude Code honours
  `Bash(python -m agentteams.research:*)` inside *sub-agent* front matter cannot be verified from
  inside this repository, and the failure direction is unsafe — a host ignoring the scope grants
  *more* than intended. The policy disclosed that; nothing would have noticed if it changed. The
  snapshot now records whether upstream docs show evidence of command-scoped permissions, with
  status `evidence-found`/`no-evidence` and never `supported`: a marker on the page could be
  describing slash commands rather than sub-agents, so only a human may upgrade the claim.

### fixed (downstream: the two research teams that motivated the review can now retrieve)

- `researchteam` and `GeneralResearchTeam` both classify as `project_type: research`, both produce
  bibliographies, and both had `capabilities: null` — so neither generated a `research-analyst`
  and neither had any external retrieval. Added `research_verification` to each `brief.json` and
  regenerated with `--update --merge`. Local only; nothing committed or pushed in either repo.
- **Discovered doing so:** agent-file YAML front matter lies *outside* every `AGENTTEAMS` fence,
  so `--update --merge` preserves it verbatim and **a template `tools:` change never reaches an
  already-generated team.** The fenced body content (the external-retrieval quality gate) updated
  correctly while the tool grant did not; both files needed hand-editing. This silently bounds the
  reach of any capability fix expressed as a tool grant. Logged to
  `references/agentteams-remediation-log.csv`, along with the finding that `GeneralResearchTeam`
  is not under version control at all.

### added (external retrieval: the teams this framework generates could not search the web)

A review of every external-retrieval path (`references/plans/external-retrieval-expansion-2026-07-30.report.md`)
found the capability was real but unreachable. Remediation plan and its adversarial/conflict
audit: `tmp/by-week/2026-W31/external-retrieval-remediation.plan.md`.

- **`agentteams/research/backends.py` — a search fallback chain, where there was one endpoint.**
  Measured 2026-07-30, the query `"retrieval augmented generation 2026 best practices for local
  code search"` returned zero results on both its original and its `_broaden`-halved form:
  DuckDuckGo challenges long multi-concept queries — exactly the shape a research agent produces
  — and a single halving does not clear it. Search now tries every available backend on the
  original query *before* altering the query at all (switching provider loses nothing;
  broadening discards search terms), and only then broadens **progressively** down to the floor.
  The zero-configuration chain is `duckduckgo` → `ddg_lite`, both key-free: a fallback that
  requires setup is not a fallback. `searxng` and `brave` join only when
  `AGENTTEAMS_SEARXNG_URL` / `AGENTTEAMS_BRAVE_API_KEY` are set, and always rank after the free
  endpoints. An honest zero still does **not** trigger broadening — that would only produce less
  precise nothing at the cost of more load on the endpoint whose rate limit caused the problem.
- **`agentteams/research/scholarly.py` + `python -m agentteams.research scholar` — OpenAlex,
  Crossref, arXiv.** This framework generates literature-review teams that emit
  `bibliography.bib`, and Constitutional Rule 5 forbids unverifiable citations, but nothing in
  the package could reach a scholarly index — a general web search returns a *page about* a
  paper, not the paper's record. Key-free sources only; built on `security_refs.py`'s proven
  exact-match host allowlist + response size bounds. Absent fields stay absent (`year=None`, not
  a guess), which is the property that makes the output safe to cite from. Retraction status is
  **not** checked and the CLI says so on every call.
- **`agentteams/research/cache.py` — TTL disk cache (6 h default).** Every search and fetch was
  a cold round trip. Because this persists untrusted third-party bytes: SHA-256 hex filenames
  only (no external text reaches a path component), corrupt/oversized/expired entries degrade to
  a **miss** rather than an error, atomic writes, gitignored directory, and
  `AGENTTEAMS_RESEARCH_NO_CACHE=1` to disable. `tests/conftest.py` disables it suite-wide so a
  warm entry cannot silently satisfy a test that asserts a transport was called.
- **A `retrieval` tool token — generated agents can now search, without gaining a shell.**
  The vocabulary was `read | edit | search | execute | todo | agent`, where `search` maps to
  `("Grep","Glob")` — *local file* search. Nothing granted external retrieval, so it was reachable
  only by an agent holding `execute` (full `Bash`). `retrieval` maps to the scoped
  `Bash(python -m agentteams.research:*)`. Granted to `tool-doc-researcher` (whose charter is
  locating official docs) and `reference-manager` (citation verification) — both previously had
  external-verification jobs and no way to reach the network. Read-only auditors are deliberately
  excluded; `execute` absorbs the token rather than emitting a misleading `Bash, Bash(...)`.
- **`references/retrieval-transport-policy.md` — the no-MCP decision, recorded.** External
  retrieval here is CLI-mediated; neither MCP servers nor host-native `WebSearch`/`WebFetch` are
  the transport. Written down because a gap and a decision look identical from the outside: the
  next agent reading the review would otherwise read "no transport wired up" as an oversight and
  add one. `tests/test_retrieval_transport.py` enforces it, and includes a test proving the guard
  can still fail (it strips docstrings/comments, so the policy prose it protects does not trip it).
- **Archetype allowlist presets** — `SOFTWARE_CONFIG`, `RESEARCH_CONFIG`, `DATA_CONFIG`, and
  `config_for_project_type()`. `DEFAULT_CONFIG` (four general-interest domains, no primary
  repositories) reduced `reputable_sources()` to "one general search filtered to Wikipedia and
  three wire services". Its contents are **unchanged** — it is the default argument of
  `ReputableSourceAllowlist.__init__`, so editing it would silently change every existing caller.
- **A `research-capability-unset` manifest advisory.** `research-analyst` is gated on an explicit
  `capabilities: ["research_verification"]` opt-in and no inspected team declared it — including
  two literature-review teams. It **advises, and does not auto-enable**: selecting the archetype
  pulls a real runtime dependency into the generated project, which is why the flag is opt-in in
  the first place. Requires a new `advisories` property in `team-manifest.schema.json`.
- **Provenance is now emitted and required.** `python -m agentteams.research search` prints
  `backend=`/`cached=`/`query_used=`/`tried=` on stderr for every query, and the
  external-retrieval quality gate requires carrying it: a cached result may be six hours old, and
  a `query_used` that differs from the query issued means the endpoint challenged the original
  and the tool retried with a **broader** one — weaker evidence, and the summary has to say so.

Also: bounded concurrency (max 4) in `reputable_sources()`, which previously fired
`len(repos) + 1` simultaneous requests at one free endpoint per topic — plausibly a contributor
to the challenges above.

### corrected (a finding in the 2026-07-30 review was wrong)

- **F6 ("`embedding-vector` is declarable but unimplemented") is withdrawn; no code changed.**
  `retrieval_integration.mode` does not describe a capability agentteams provides — it describes
  the **consuming project's** retrieval stack. `ingest.py:679` reads the target project's files
  and `ingest.py:738` sets `embedding-vector` when *that project's* code mentions
  faiss/chroma/pinecone/qdrant/weaviate/milvus, so the generated `@retrieval-integrator` can
  validate its contract. The review conflated this with `code_index.py`'s separate — and already
  explicitly labelled — reservation of `vector_model_id`/`vector_dim` for a future dense tier.
  The report carries the correction inline rather than silently dropping the finding.

### added (governance instruments)

- **`agentteams/living_doc.py` + an `audit.py` check — living-document conformance.**
  Every generated team's constitution forbids dated snapshots, resolved-issue
  archaeology and dated fix logs in agent docs. Nothing verified it. Scope was set
  by measurement, not assumption: a date scan over the module's own emitted teams
  returns 65 signals and one true violation across all files, and exactly that one
  violation when restricted to unfenced `.agent.md` prose. 60 of the 65 are CVE
  rows inside the `threat_intelligence` fence, which re-renders on every update and
  so cannot go stale. Findings are warnings, never errors. The policy's fourth
  prohibition (hardcoded volatile state) is deliberately excluded — whether a value
  is volatile is a claim about the future.
- **`architecture.detect_import_cycles` + `module_level_edges` — CH-13.** The first
  implementation walked `ArchitectureGraph.edges` and reported three cycles in this
  package, none actionable: `analyze`/`output_plan`, `emit`/`fence_inject` and
  `cli.commands`/`stale_remediate` are all deliberately broken by deferring one
  side's import inside a function. `ast.walk` cannot tell a load-time import from a
  deferred one. `module_level_edges` reads only direct children of each module body;
  the package reports zero load-time cycles, which is the true state.
- **`agentteams/update_report.py` — `update.report.md`.** `--fleet`,
  `--bridge-merge` and `--recipe-check` all leave reports; `--update`, the most
  common operation, left none. Preserved fences, skipped legacy files and
  retrofitted markers went to stdout and died with the scrollback. Silent on a
  clean run; never affects an exit code.
- **`scripts/verify_audit_ledger.py`** — report-only structural verification of
  `template-chapter-audit.csv`. Its own first run reported "no structurally false
  claims" while 38 of 44 rows were unverified; `unreviewed` now surfaces as REVIEW.
- **`scripts/check_session_obligations.py`** — reports constitutional obligations
  with no supporting artifact. Reports *absent evidence*, not violation, and always
  exits 0: gating would try to prevent what the principle says can only be made
  accountable.
- **`references/code-hygiene-mechanization.reference.md`** — the 28 `CH-` rules
  classified with a reason each, so a deliberate boundary is distinguishable from a
  backlog. **Refiled** out of `templates/domain/` (see *fixed*, below).
- **`tests/test_code_hygiene.py`: CH-01, CH-11, CH-15** as path-based rules, each
  stating what its PASS establishes. CH-18 was attempted and rejected — a naive
  version-token probe flags dated work summaries.
- **`tests/test_template_emission_coverage.py`** — every template under
  `agentteams/templates/` must be reachable from `output_plan.py`, or allowlisted
  with a reason. The existing orphan advisory runs output-side only, so a template
  no plan reached was undetectable; one had been sitting in `templates/domain/`.
  The first version of this check **passed a planted orphan**: it derived the valid
  archetype set from the templates directory, making every file that existed
  trivially "reachable". Archetypes and tool categories are now read from
  `analyze.py` instead, and three negative controls verify the check fails when it
  should.
- **Three self-consistency guards on the mechanization classification** in
  `tests/test_code_hygiene.py`: every catalogue rule appears exactly once, the
  Summary counts are derived from the table rather than tallied, and any row
  claiming a check exists must name it. The third found five rows asserting
  coverage with no implementing surface.

### changed (retrieval hot-path validation cache)

- **Memory-index and code-index schema validation is cached (measured 2026-07-16), skipping re-validation
  of an unchanged index on the `--query-index` / `--query-code` hot paths.** `jsonschema`
  validation dominated every query (~95% of wall-clock on a real ~16 MB memory index; the
  ranking math it guards is <0.1%), and it re-ran on every read even when nothing changed.
  A tiny fail-open `.vcache` sidecar records the `sha256(content):sha256(schema)` pair that
  last passed and short-circuits when it still matches — **~28.8× faster warm reads**
  (~2,269 ms → ~79 ms). The code-index variant folds the manifest **and every partition**
  into a multi-file key, so a change to any file or the schema forces a re-validate. The
  sidecar holds only the two hashes (no machine-specific data), is written only after a validation success — a stale/torn/missing sidecar
  always falls back to full validation. Public API, all `schemas/*.json`, and the on-disk
  artifact byte formats are unchanged (internal `_`-prefixed functions only). The shared
  validation + cache machinery was carved into `agentteams/cli/schema_cache.py`. New guards
  `tests/test_memory_index_validation_cache.py` (9) and `tests/test_code_index_validation_cache.py` (8).
  Ref: `SEC-CM-2026-07-16-AGENTTEAMS-VCACHE-001`.
  - **Not adopted: `fastjsonschema`.** Evaluated as the lever for the validations the
    content cache *can't* skip (cold first reads, rebuilds, build-time writers) but declined:
    it would break the deliberate single-runtime-dependency posture and carries supply-chain
    conditions (it `exec`s compiled schema code — needs pin+lockfile, trusted-tree schema,
    and accept/reject parity fixtures against `jsonschema`). Measured trade-off: caching a
    compiled `Draft7Validator` saves only ~1.4%, while *not* validating (the content cache)
    saves ~28.8× — so the cache already captures the realistic win. The single
    `_validate_against_schema` seam is left ready for a future opt-in behind an optional extra.

- **The delivery-receipt, eval-suite, and model-routing writers now write atomically.**
  They used a bare `write_text(json.dumps(...))` that could leave a torn file on a crash
  mid-write; all three (and the memory-index writer) now route through the shared
  `atomicio._atomic_write_text` (temp-in-same-dir + fsync + mode-preserving + `os.replace`),
  bringing the whole artifact-writer cluster to a uniform atomic-write posture.
- **Recorded 2026-07-29, integrated from an unpushed commit.** The code above landed via
  PR #56 on 2026-07-16; its changelog entry never did, so a ~28.8× retrieval speed-up and
  a documented *not-adopted* decision went unrecorded for two weeks. The entry is
  reproduced from commit `f3e8729` with one claim corrected: it asserted the sidecar
  "is gitignored in every consumer", which is **false** — no template emits that ignore
  rule, and the `jameslcaton` consumer has three `.vcache` files tracked. Logged to the
  remediation log rather than fixed here.

### changed (audit ledger)

- **`template-chapter-audit.csv` gains `disposition`, `implementing_surface` and
  `verified_on`.** "No template for X" and "X is not implemented" are different
  claims and the schema could not express the difference. Four rows asserting the
  first were read as the second, and that framing reached the book: `TA-033`/`034`
  were discharged by `fleet.py` and `TA-035` by `drift.py` while ranked as
  high-severity gaps. All 44 rows now carry a disposition; 0 unreviewed.
- `TA-036`–`TA-038` verified genuinely **absent** rather than reframed — the
  assumption that they shared the `TA-033` conflation held for only one of the four.
- `TA-003` corrected: it understated the security rule count by five.

### fixed

- **13 of the 17 constitutional rules were invisible to deletion (security review S4.2).**
  `_detect_unfenced_drift` is silent on modified files and a tampered file is modified by
  definition, which is why `_detect_deleted_constraints` exists as its complement — but it
  matched on vocabulary (`⛔ | read-only | PRIORITY LEVEL | HALT | MUST NOT | never`), and
  the rules do not speak that way. **Rule 1, "`@security` before destructive operations" —
  the rule the entire enforced stack rests on — says "require", so it matched nothing.**
  Deleting it was neither restored (the list is unfenced by design, so projects can extend
  it) nor reported. Now tracked by *shape* (`NUMBERED_RULE_RE`) rather than by adding
  words: broadening the vocabulary would trade a false negative for false positives on
  prose, and a notice that fires on rewording is one people learn to skip. Measured:
  constitutional rules tracked 4/17 → **17/17**, files firing against the real deployed
  team 5 → 5 (**zero added noise**).
- **`--shrink-policy=preserve` trusted the disk over security contracts (S4.4).** It
  favours on-disk content whenever the template body looks materially smaller — correct
  for operator enrichment, inverted for a security contract, because the heuristic cannot
  distinguish enrichment from tampering when they are the same operation. Appending one
  backticked token to a fenced security region suppressed that fence's update indefinitely
  behind a scrolling notice. `_TEMPLATE_AUTHORITATIVE_FENCES` (`invariant_core`,
  `security_authority`, `security_rules_invariant`, `security_verdict_contract`) is now
  exempt from preservation. Kept **separate** from `_LIVE_DATA_FENCES` as the review
  insists — that set means "upstream feed rotation is expected", a different claim, and
  conflating them would obscure both. Ordinary fences are still preserved, pinned by a
  second test.

- **`test_committed_check_reports_describe_the_committed_source_state` broke CI on
  `main` and I did not notice for four merges.** The test compares a committed
  bridge-check verdict against the working tree's source team — but `.github/agents/*`
  is gitignored here (1 of ~35 files tracked), so a fresh clone has nothing to hash and
  the digest degrades to `sha256("")` = `e3b0c442…`, which can never match a committed
  report. It passed locally only because those files exist on the author's disk.
  `main`'s CI went red at `f145434` (the commit introducing it) and stayed red through
  three further merges, each verified locally and reported as green while CI was not.
  The check is **inherently local-only** and now says so: it skips, with the reason, when
  the source team is absent. Skipped rather than relaxed — comparing against an empty
  tree would "pass" only by making the assertion meaningless, which is the failure mode
  the file exists to prevent. Verified both ways: skips in a simulated fresh checkout,
  runs and passes locally.

- **Eight API-doc signatures had drifted from the code, two of them fatally.**
  `drift.compute_structural_diff` was documented with parameter `manifest` where the
  code says `new_manifest`, and `enrich.build_tool_catalog` with `packages` where the
  code says `package_names` — a documented keyword call that raises `TypeError` is worse
  than an omission. Also corrected: `emit_all` (missing `auto_fence_legacy`),
  `backup_output_dir` (missing `reason`, `framework`, `description_path`),
  `parse_dependency_manifests` (missing `*, max_depth`), `build_memory_index` (missing
  `root`), `is_index_stale` (missing `*, root`), and
  `inject_fence_markers`, whose parameters were documented in the **wrong order** —
  found only because the new guard compares ordered lists; the ad-hoc check that
  preceded it used set comparison and missed it. Every added parameter also gained a
  description, not just a heading entry.
- **`docs_src/assets/feature-summary-table.md` stated a total of `~126` while its own
  addends sum to 125.** The inventory's own note warns readers that hand-maintained
  counts drift — and the summary committed exactly that defect in the line summarising
  the table above it. The governance-agent row was checked against
  `analyze.GOVERNANCE_AGENTS` and is exactly right (11, names matching).

### added

- **`tests/test_unfenced_constraint_ratchet.py`** — a constraint-bearing line outside
  every fence is a rule a project can delete and no `--update --merge` will restore.
  `security.template.md` forbids this outright; the rest of the library carries **159 such
  lines across 43 templates**. Too many to fence safely in one pass, so a ratchet records
  the per-file baseline and forbids growth, with a staleness guard that requires entries be
  *lowered* as debt is paid. Verified against a planted constraint. **A passing run
  establishes only that no new unfenced constraint was added** — the 159 remain deletable
  and unrestorable, so the security review's S4.5 stays open and this file is the
  measurement of that debt, not its discharge.

- **`.github/workflows/changelog-link.yml`** — a PR touching `agentteams/**/*.py` must
  also touch `CHANGELOG.md`. Origin: PR #56 landed the retrieval validation-cache code
  on 2026-07-16 while its changelog entry sat in an unpushed local commit until
  2026-07-29 — two weeks in which a ~28.8× speed-up and a documented *not adopted:
  fastjsonschema* decision were absent from the changelog while the code shipped.
  Nothing linked the two.
  **This check fails rather than advises, which is the one place this repository's
  instruments do so.** `check_session_obligations.py` always exits 0 and
  `advisory-pr.yml` never merges, both because gating would try to *prevent* what the
  accountability principle says can only be made *answerable*. The same principle is
  why this one fails: an advisory that can be ignored is precisely what already failed
  here. The accountability is the override, not the gate — a `no-changelog` label passes
  the check and leaves a record on the PR of who judged the change to need no entry.
  Proceeding is always available; proceeding *silently* is not. Bot-authored PRs (the
  scheduled bridge-maintenance and framework-auto-update runs) are exempt.
  Validated against real history rather than assumed: replaying **PR #56's own diff**
  through the check's conditions fails it correctly, and today's merged PRs pass.

- **`tests/test_api_doc_signatures.py`** — documented signatures must equal the real
  parameter lists, plus arithmetic guards on the feature summary. Written **before** the
  fixes and confirmed failing on the unfixed tree, so the corrections were verified by
  the instrument rather than by re-reading.
  Scope is narrow **by construction, not by allowlist**: only headings naming a
  module-level function of the module that doc covers are compared, which excludes
  methods (`graph.md`'s `to_dot()` without `self` is correct style) and same-name
  functions in other modules (`parallel_plan.to_json(schedule)` vs
  `graph.ArchitectureGraph.to_json(self)`). An allowlist would have swallowed real drift
  along with that noise. A coverage floor (90 signatures across 25+ docs) fails on a
  parser regression that would otherwise make the test silently vacuous.
- **Five new API pages** — `living-doc`, `update-report`, `output-plan`,
  `memory-index-incremental`, `cli` — plus the seven symbols added earlier today
  (`rel_to_root`, `skip_notice`, `source_state_digest`, `store_path`, `resolve_path`,
  `module_level_edges`, `detect_import_cycles`) documented in their existing module
  pages. `cli.md` describes the **module decomposition** and links to
  `cli-reference.md` for flags rather than restating them (CH-14).
- **`api-reference/index.md` now records its own coverage gaps** — the 14 top-level
  modules with no page, named individually, with the four over 400 lines called out as
  the highest-value additions. They are listed rather than written because a thin page
  that misdescribes a module is worse than a missing one. Accuracy is enforced;
  coverage is a stated gap.

- **The memory index was indexing backup snapshots: 1764 gitignored files, 83% of a
  2120-document, 51 MB committed artifact.** 1488 were
  `.agentteams-backups/` files under `examples/*/expected/`, reached because
  `memory_index_extra_dirs: ["examples"]` recursively globs `*.md` and **no scan had
  a scratch exclusion of any kind** — while `_memory_index_sources`' docstring claimed
  "never gitignored scratch areas". Backups are near-duplicates of the documents
  beside them, so they diluted BM25 scoring as well as bloating the artifact. Now
  **632 documents, 16.6 MB, 0 backups**, with retrieval verified unchanged (identical
  top-3 for a representative query).
  The rule is deliberately **not** "exclude gitignored paths": `workSummaries/` and
  `references/plans/` are gitignored here yet are the durable history the index
  exists to serve, so 276 of them are still indexed. Gitignore marks *local*, not
  *disposable*; the filter matches scratch **directory names** instead.
- **`CH-06` implemented, and `CH-03` reclassified to judgment.** CH-06's heredoc half
  is fully enforced (0 violations — a clean PASS means the rule holds); its
  ≤5-line half is a **ratchet** over 10 pre-existing blocks, so CH-06 is filed
  *partly mechanized*, not *mechanized*. CH-03's stated reason ("executable/script
  extensions in a known directory") described a **weaker rule than the catalogue
  states** — CH-03 forbids *ad-hoc* scripts while permitting production code, so it
  turns on intent, the same gap that makes CH-02 and CH-27 judgment. `CH-04` stays
  unwritten with its missing definition now named explicitly: the package has **no**
  debug-artifact pattern vocabulary, and writing one here would be inventing the
  rule's content. The mechanizable backlog is 5 → 3.
- **Every remaining real absolute home path removed from tracked files**, and a
  repo-wide guard added (`test_no_tracked_file_embeds_an_absolute_home_path`). Four
  archived baseline captures and two work summaries now use `~`; a Windows test
  fixture uses a documented placeholder. The guard flags *real* operator names while
  permitting documented placeholders (`me`, `you`, `alice`, …), because a generic
  example path is good practice — the defect is embedding a real one. The only
  allowlisted file is the memory index, whose remaining 28 occurrences are verbatim
  quotations of gitignored local plans.

### added

- **`references/plans/remediation-log-candidates-2026-07-29.report.md`** — closure
  candidates for the remediation log with per-row evidence. **No row was edited**;
  Rule 11 makes the `status` lifecycle maintainer-owned. Recommends closing rows 9
  and 44, leaving 15 and 3 open with reasons, and records that row 15 **misattributes
  the memory-index generator** to a mechanism outside the package when it is
  `memory_index.py::build_memory_index` — a future pass following its touch points
  would look in the wrong place. Three new rows appended (append-only, proven), one of
  which logs a **CH-05 defect: five independent copies of the "what is scratch"
  vocabulary** across `backup.py`, `fleet.py`, `baseline.py`, `audit.py` and now
  `cli/artifacts.py`.

- **`memory-index.json` stored absolute document paths — 2150 of them in a committed
  artifact.** `build_memory_index(..., root=)` now stores each document's `path`
  relative to the project root, and `is_index_stale`/the incremental sed updater
  resolve against the same root. Relativizing happens **inside** the builder rather
  than at the serialization boundary, so `source_fingerprint` — derived from the
  document paths — describes the paths actually stored; fixing only the boundary
  would have repeated the one-field-fixed mistake from the bridge manifest. The
  committed index went from 2135 absolute occurrences to 36, and **all 36 remaining
  are inside `snippet`/`paragraphs`**: verbatim excerpts of source documents whose own
  prose names absolute paths. Those are deliberately left alone — rewriting a
  quotation would make the index misquote its source while `source_hash` still
  attested to the original. A legacy index with absolute paths degrades to a full
  rebuild (which rewrites it in the new form) rather than producing a mixed index.
- **Also genericized:** `docs_src/claude-privileges.md`'s example permission string,
  two `tests/test_scan.py` PII fixtures (`/Users/alice`, matching `scan.py`'s own
  docstring example — the regex is username-agnostic, so the test is unweakened), and
  `tests/test_learnpython_generation.py`'s live-repo constant, now resolved from
  `Path.home()`.

### changed

- **The bridge check report records a source-state digest instead of a wall clock,
  and `--bridge-check` no longer writes when nothing changed.** The previous fix
  recorded `Checked at: <timestamp>`, which created a second problem while only
  half-solving the first: a command documented "read-only; produces a freshness
  report" then rewrote a tracked file on **every** invocation, and a timestamp
  conveys staleness only to a human who opens the file and does the arithmetic —
  which is exactly what nobody did for the week the copilot-cli report sat wrong.
  Recording `Source state: <digest of the source files>` is deterministic, so
  re-checking unchanged sources produces identical bytes and the report is skipped;
  and it is machine-comparable, so
  `test_committed_check_reports_describe_the_committed_source_state` fails when a
  committed verdict no longer describes the tree. That test reads `git show HEAD:`
  deliberately — comparing a freshly generated report against the tree recomputes
  the digest from that same tree and could never disagree. `Manifest generated at`
  is retained; it comes from the manifest and answers a different question.

- **The remaining two bridge manifests regenerated; all three pairs now PASS.**
  `copilot-vscode-to-copilot-cli` was 6 rows stale and `copilot-vscode-to-goose` 3.
  Verified as pure time drift before writing rather than after: all three pairs carry
  identical 35-row sets, goose's stale set is a strict subset of copilot-cli's, and
  every stale row is a source file modified after that pair's own `generated_at`
  (copilot-cli 2026-07-22, goose 2026-07-25). No structural divergence was being
  masked. `--bridge-merge` throughout — Pre-Flight Check 4 forbids refresh here
  because `.github/copilot-instructions.md` is gitignored and was never committed, so
  it has no git safety net at all.
- **A committed `bridge-check.report.md` said PASS while its manifest was 6 rows
  stale — and nothing in the file revealed how old the verdict was.** The report is a
  snapshot of one run, written only when `--bridge-check` runs, but its wording is
  present-tense: "Bridge artifacts are fresh and consistent with source files." The
  copilot-cli check last ran on 2026-07-22 when that was true, and the verdict then
  sat unchallenged for a week. This is how the drift went unnoticed. Reports now
  record both the check time and the manifest's `generated_at`, and a passing report
  caveats its own freshness.
- **`1937cbc`'s OPSEC fix was incomplete: six tracked bridge artifacts still embedded
  an absolute home path.** That commit relativized `bridge-manifest.json`'s
  `source_dir` — one field in one file — while the sibling artifacts the same run
  writes kept leaking the operator's username and directory layout:
  `agent-inventory.md`'s source-file column and `bridge-merge.report.md`'s per-file
  lines, across all three pairs. Extracted `bridge.rel_to_root()` as the shared form
  so the three cannot drift again, regenerated every pair, and added a guard that
  scans **every** file under `references/bridges/` rather than the manifest alone —
  a one-field fix on a multi-file emission is exactly how this recurred.
  `.claude/agents/references/memory-index.json` still carries the same leak; it is a
  different subsystem and is reported, not changed here.

- **The bridge's mode banner could not name the mode, and its skip notice
  recommended the destructive one.** Both sit on the safety path that
  `references/bridge-refresh-safety.md` exists to enforce, and both were found while
  running a `--bridge-merge` under that policy. `cli/commands.py` printed only
  `check` or `generate`, so a `--bridge-merge` displayed as `generate` and a
  `--bridge-refresh` was indistinguishable from a bare invocation — the banner could
  not confirm the intended mode ran. And when `--bridge-merge` correctly skipped
  unfenced target files, `bridge.py` advised "Pass `--bridge-refresh`… recommended
  when bridge state is incomplete or stale" — pointing the operator at the
  destructive mode for exactly the user-authored files merge mode had just protected,
  under the condition where the advice is most likely to be followed. That is the
  mechanism of the 2026-05-27 incident, still live in the tool. The banner now names
  all four modes and states that refresh overwrites; the notice explains the skip as
  the merge contract and warns against refresh. `bridge.skip_notice()` was extracted
  so the text is testable without a live bridge run, guarded by
  `tests/test_bridge_mode_safety.py` (7 tests, including one asserting the helper is
  actually wired into `run_bridge`).

- **`references/agentteams-remediation-log.csv`**: two rows carried unquoted commas
  that split them into eight fields against a six-column header, so any
  `DictWriter` consumer raised. Repaired under a proof that every non-comma
  character is preserved. The underlying gap — nothing validates the log's shape —
  remains logged as open.
- **`docs_src/api-reference/feature-inventory.md`**: version baseline read `0.1.0
  (2026-04-15)` at `1.0.0rc6`. Re-baselined, and the per-category counts are now
  declared hand-maintained rather than presented as derived.
- **The mechanization classification was misfiled, misclassified, and its severity
  overstated — retracting all three.** The audit that produced it asserted "this
  template ships to consumers" as the finding's severity basis. **That was false.**
  It was the only domain template absent from `output_plan.py`, so it never
  rendered into any team; and its content names agentteams internals
  (`tests/test_code_hygiene.py`, `audit.py::_check_*`) that no generated team has,
  so registering it for emission would have been the wrong repair. Refiled to
  `references/`. Separately, a re-read of every test in `tests/test_code_hygiene.py`
  found **four rules misclassified** — CH-05 and CH-24 filed as `judgment`, CH-07
  and CH-22 as unwritten backlog, while each had an enforcing test — all in the
  direction that overstates the backlog. Filing them then exposed that `partly
  mechanizable` was carrying two unrelated meanings, now split into
  `partly mechanized` (a check exists, narrower than the rule) and `partly
  mechanizable` (no check, partly specifiable).
- **`references/agentteams-remediation-log.csv` shape is now validated**
  (`tests/test_remediation_log_shape.py`), closing the gap the entry above left
  open. Structural only — field count, ISO dates, populated required fields, no
  embedded newlines. It asserts **nothing** about `status`, because Rule 11 makes
  that lifecycle maintainer-owned; a new status value passes unchanged.

### fixed (packaging/build hygiene)

- **`build/` is now gitignored.** `.gitignore` covered `dist/` and `*.egg-info/` but not
  setuptools' intermediate staging tree, which `pip wheel .`, `pip install -e .`, and
  `python -m build` all create at the repo root. Because it holds a full copy of `agentteams/`,
  and the code-hygiene guards enumerate sources via
  `git ls-files --cached --others --exclude-standard`, an unignored `build/` made three
  `tests/test_code_hygiene.py` tests fail with phantom duplicate definitions — e.g. "Framework
  registry must be a single dict literal in registry.py; found definers:
  `['build/lib/agentteams/frameworks/registry.py', 'agentteams/frameworks/registry.py']`". Any
  contributor who built a distribution locally hit this. No test change was needed; the guards
  already honour gitignore.

### added (packaging regression guard)

- **`tests/test_console_script_entrypoints.py`** asserts that every `[project.scripts]` target
  module is covered by the packaging config (`py-modules` or `packages.find`) and that the named
  attribute exists and is callable. The `agentteams` script resolves `build_team:main`, and
  `build_team` reaches the wheel only via `py-modules` — a split that lets an entry point
  silently reference an unshipped module, surfacing only post-install as a bare
  `ModuleNotFoundError`.

### docs

- **Getting Started documents the stale-editable-install failure mode.** An editable install
  records the path it was installed from; if that tree is deleted (a cleaned-up git worktree, a
  checkout under `/tmp`), every import through it fails and `agentteams --version` reports
  `ModuleNotFoundError: No module named 'build_team'`. The message names `build_team` only
  because it is the first import attempted — `import agentteams` fails identically — which
  misdirects diagnosis toward packaging. Added the `pip show` check and the reinstall recovery.

### fixed (bridged Goose teams could not retrieve anything: capability existed but was invisible)

- **`agentteams/bridge.py` now advertises `agentteams.research` in the entry files it writes.**
  `agentteams/frameworks/goose.py` already documented the module (search / text-extracted fetch /
  browser rendering) in the hints it generates, but the **bridge** path writes its own
  `AGENTS.md`/`.goosehints` and silently dropped that guidance. Measured on this repo:
  `grep -c "agentteams.research" AGENTS.md .goosehints` → `0`, `0`, and a live failing turn's
  20,258-char system prompt contained **zero** occurrences of "research". The agent had a working
  general web-search capability installed and no idea it existed — so it guessed URLs, scraped a
  homepage, and put 29,654 chars of navigation HTML into its own context (54% of the whole
  conversation) without ever retrieving the answer. Also adds guidance to prefer extracted text
  over raw HTML, and that relevance ranking is not recency for "most recent X" questions.
- **`agentteams/research/search.py`: a blocked search is no longer reported as "no results".**
  DuckDuckGo answers a challenged request with **HTTP 202** plus an interstitial page, so
  `raise_for_status()` never fires (202 *is* success) and the challenge page simply parsed to
  nothing. Measured deterministic: a long specific query returned 202 with zero results on 4/4
  attempts while a shortened form returned 200 with 10 results. New `web_search_verbose` retries
  once with a broadened query and returns a note explaining the fallback or the block; the CLI
  prints it to stderr so stdout JSON stays parseable. `web_search` keeps its list contract.
- **`fetch_text`'s download cap raised from 40,000 to 400,000 bytes.** `max_bytes` bounds the
  *download*, and at 40 KB a real Wikipedia article extracted to **342 chars of navigation
  chrome with zero body content** — with no error to distinguish "the page lacks that" from "we
  never downloaded the part that has it". The same page yields 17,744 chars including the full
  results table at the new default. `max_chars` (context guard, still 4000) is unchanged and
  independent; `--max-bytes` is now exposed on `python -m agentteams.research fetch`.
- **Measured effect on the original failing question** ("top 10 finishers at the most recent
  NASCAR Cup race at Las Vegas"), CLI runs before (7) and after (8):

  | | correct | silent dead turn | partial/wrong |
  |---|---|---|---|
  | before | 2/7 | 3 | 2 |
  | after | 4/8 | 2 | 2 |

  Retrieval now works — the agent discovers the tool, verifies it with `--help`, searches, and
  fetches the right page. On `qwen/qwen3.6-27b` that was **not** enough to meet a pre-set ≥7/8
  criterion. Re-running the identical harness on **`qwen/qwen3.6-plus`** gives **8/8 correct, 0
  dead turns, 0 partial** (34–188s) — so the retrieval fixes were *necessary but not sufficient*,
  and model capability was the second binding constraint. Plus also volunteered the recency check
  ("the next Las Vegas Cup race is October 4, 2026, which hasn't occurred yet") unprompted.
  Caveat: `qwen3.6-plus` is served by **Alibaba only**, so with the route proxy's
  `allow_fallbacks: false` an Alibaba outage is a hard failure rather than a reroute. The residual failures are the separate dead-turn leak
  (unfixed; it recurs even on an allowlisted backend) and partial answers. Running through
  `scripts/goose-run-resilient.py` was also measured (3 correct / 1 dead / 4 partial): it roughly
  halves dead turns but converts them into partial answers rather than correct ones, so it is not
  a net win for answer quality here.

### fixed (Goose/OpenRouter: backend routing is the real dead-turn lever; corrected false docs)

- **`docs_src/goose-cloud-providers.md` no longer documents `OPENROUTER_PARAMETERS` as working.**
  It is **inert in Goose 1.37.0** — the key does not appear in the binary, and neither a nested
  `provider` block nor a top-level `transforms` override reaches the outgoing request body
  (verified with an isolated config via `XDG_CONFIG_HOME`; a `model` change in the same file
  confirmed goose had read it). Previous revisions instructed users to set a key that does
  nothing.
- **Corrected `references/plans/goose-openrouter-tool-call-reasoning-leak-2026-07-24.report.md`.**
  It reported `provider.require_parameters: true` as "tested negative" for the dead-turn bug.
  The setting was never transmitted, so that experiment never ran; the observed leak rate was
  the unmitigated baseline. The conclusion stands, the stated reason does not.
- **New `scripts/goose-openrouter-route-proxy.py`** — the mitigation that actually works, and
  the only one that covers **all** goose surfaces. `OPENROUTER_HOST` *is* honored, so this local
  proxy injects OpenRouter provider routing at the transport layer, reaching the CLI, the
  desktop app, and `goose acp` (the VS Code extension). Allowlist-first, streaming-safe, and it
  never overrides a caller-supplied `provider` block. Opt-in: it is a process that must stay
  running, and that tradeoff is stated in the docs rather than decided for the user.
- **Which OpenRouter backend serves you materially changes tool-calling reliability.** Replaying
  one real captured agent payload (11 messages, 24 tools, reasoning on) 12× per backend against
  `qwen/qwen3.6-27b`: Alibaba, CoreWeave and Morph 12/12 clean; Chutes 1/12, Phala 1/12 and
  **SiliconFlow 3/12** leaked the tool call into the reasoning stream. Quantization does not
  explain it (CoreWeave is fp8 and clean; Phala is unquantized and leaks). "Clean" is bounded,
  not proven — 0/12 still leaves a ~22% upper bound at 95% confidence.
- **New `--providers MODEL` flag on `scripts/goose-openrouter-preflight.py`** lists a model's
  upstream backends, flagging that `tools=yes` means the parameter is *accepted*, not that the
  model's native tool-call template is extracted correctly.
- **`scripts/goose-run-resilient.py` now documents its scope limit**: it wraps `goose run`, so it
  cannot protect the VS Code extension / any ACP client. `docs_src/goose-system-prep.md` §6 now
  leads with backend routing and marks the wrapper as CLI-only.

### added (Goose dead-turn resilience: OpenRouter reliability docs + auto-continue runner)

- **New `scripts/goose-run-resilient.py`**, shipped unconditionally with every Goose team
  agentteams generates or bridges (`GooseAdapter.extra_output_files()`, read from disk so the
  shipped copy can never drift from the tested one). Detects a "dead turn" — goose's own logged
  response has no `text` and no `toolRequest` content, the confirmed signature of a model
  trapping its tool call inside `<tool_call>` text in the reasoning stream instead of the
  structured API field, silently with no error anywhere — and auto-resubmits `"Continue"` in the
  same session, up to a retry cap. Fail-closed: any log it can't confidently classify (missing,
  empty, malformed, or an unrecognized shape) is treated as alive, never as dead. Confirmed
  against OpenRouter + a reasoning-capable model live-testing (4/4 real dead turns recovered
  across 8 trials); behavior on other providers, including local Ollama, is untested but
  structurally can't misfire thanks to the fail-closed design.
- **New documentation**: `docs_src/goose-cloud-providers.md` gains a provider-reliability
  section; `docs_src/goose-system-prep.md` gains a new §5a introducing the bundled runner and a
  new §6 troubleshooting row distinct from the existing colon-slug early-stop row.
  *(Superseded within this same release by the `### fixed` entry above: that section's original
  `OPENROUTER_PARAMETERS`/`require_parameters` guidance was wrong — the key is inert in Goose
  1.37.0 — and has been rewritten as "Reliability: choosing which upstream backend serves you".)*
- **Fixed in the same pass**: `agentteams/fences.py`'s auto-fence-on-update retrofit would have
  corrupted the shipped script into invalid Python on the next `--update`/`--update --merge` (same
  failure class already solved for generated SVGs — the new path is now in
  `_MACHINE_MANAGED_MERGE_OVERWRITE_PATHS`), caught by `@framework-adapters-expert` audit before
  merge, not after.
  - Full design/audit trail (plan-level + implementation-level audits):
    `tmp/by-week/2026-W30/goose-openrouter-resilience-integration.plan.md`. Underlying
    investigation: `references/plans/goose-openrouter-tool-call-reasoning-leak-2026-07-24.report.md`.

### added (mandatory external-retrieval quality gate)

- **New shared reference doc** (`references/external-retrieval-quality-gate.reference.md`,
  emitted unconditionally for every team) defining a mandatory final-step audit gate for any
  summary built from externally-retrieved information: hand the draft off to `@adversarial` and
  `@conflict-auditor`, revise and re-audit any claim with a finding until it passes, and escalate
  (rather than loop forever) a claim — tracked by its stable underlying assertion, not by its
  citation URL — that still fails after 3 consecutive cycles.
- **Wired into the three agents that retrieve external information**:
  `research-analyst.template.md` (new Procedure step, before presenting findings),
  `tool-doc-researcher.template.md` (before the `@agent-updater` hand-off — don't persist an
  unaudited `docs_url`/`api_surface`/`common_patterns` value), and `content-enricher.template.md`
  (before Step 5 Validate, scoped to the externally-looked-up tool-documentation fields only).
- The new content in `research-analyst.template.md`/`tool-doc-researcher.template.md` is wrapped
  in its own explicit fence (`external_retrieval_quality_gate`) so it actually propagates to an
  already-generated team via `--update --merge`, not just a fresh render — both templates already
  had a `memory_index_consultation` fence, which (per this project's own fencing convention)
  suppresses the automatic whole-body default fence for the rest of the file. Verified with a new
  test exercising the real merge path, not just a fresh render.
  - Full design/audit trail: `tmp/by-week/2026-W30/external-retrieval-quality-gate.plan.md`.

### fixed (unified tool-metadata catalog: unconditional resolution + npm registry tier)

- **Consolidated three drifted-apart static tool-metadata catalogs** (`analyze.py`'s
  `_KNOWN_TOOL_METADATA`, `enrich/_tools.py`'s `_TOOL_CATALOG` and `_CANONICAL_DOCS`) into one,
  `agentteams/tool_metadata_catalog.py`. Previously only `_KNOWN_TOOL_METADATA` (13 entries) was
  reachable on the unconditional generation path — every other known-package entry, including
  `boto3`, `requests`, `sqlalchemy`, `fastapi`, `tensorflow`, `torch`, `scikit-learn`, was only
  ever consulted inside the opt-in `--enrich` pass, so a plain `--overwrite`/`--update` run
  rendered `{MANUAL:TOOL_DOCS_URL}` for those packages even though a zero-network, already-known
  answer existed. The merge resolves 5 conflicting `docs_url` entries (preferring the richer
  `_TOOL_CATALOG` value, discarded value recorded in a comment) and adds a new `pytest` entry.
- **New npm registry fetch tier** (`enrich/_tools.py::_fetch_npm_metadata`, opt-in via `--enrich`,
  same as the existing PyPI tier) — closes a real gap for JS/TS dependencies detected via
  `package.json`, which no prior fetch path could ever resolve. Handles scoped packages
  (`@scope/name`) correctly. `build_tool_catalog`'s PyPI→npm fallback merges fields rather than
  replacing wholesale, so a real `api_surface` PyPI returns alongside an empty `docs_url` survives
  the npm fallback instead of being silently discarded (post-implementation audit finding).
- **Fixed `_slugify` mangling scoped package names**: `_slugify('@typescript-eslint/parser')`
  silently concatenated to `'typescript-eslintparser'` (both `@` and `/` were simply deleted). New
  `_slugify_tool_name` (used only for tool-doc slugs) treats `@`/`/` as separators instead,
  producing `'typescript-eslint-parser'`, with byte-identical output to the original `_slugify` for
  every name that doesn't contain `@`/`/`.
- **Orphan-file advisory extended to reference docs**: `--update`'s existing orphan-*agent*-file
  advisory (`*.agent.md` only) now also detects+reports `references/ref-*-reference.md` files left
  behind when a tool is removed from the brief — previously invisible to `--update`, `--enrich`,
  and `SETUP-REQUIRED.md` alike, since all three only ever look at the current run's rendered
  output. Detection + reporting only (matches what the agent-orphan advisory already does; neither
  it nor `--prune` deletes anything today).
  - Full design/audit trail: `tmp/by-week/2026-W30/tool-doc-catalog-remediation.plan.md`.

### added (Playwright-backed browser rendering, CLI-managed)

- **New `agentteams.research.browser` module** (`agentteams/research/browser.py`) — closes the
  gap left by `agentteams.research.fetch_text`/Goose's `computercontroller.web_scrape`, neither of
  which executes JavaScript: `browser_fetch`/`browser_screenshot` render a page in real Chromium
  via Playwright and return extracted text or a screenshot. Deliberately **CLI-managed, not
  MCP-managed** (explicit operator preference) — invoked as `python -m agentteams.research browser
  "<url>" [--headed] [--wait-until ...] [--screenshot PATH]`, gated behind a new, separate
  `agentteams[browser]` optional extra (composes on top of `agentteams[research]`; not folded into
  it, so text-only search users never pay for Playwright's browser-binary download) plus a
  required one-time `playwright install chromium`.
  - **Two-layer SSRF guard**: the initial URL is checked with the now-public
    `agentteams.research.search.is_public_https` before a browser is even launched, *and* every
    subsequent request the live page attempts (redirects, subresources, page-initiated JS
    `fetch`/`XHR`) is re-checked by the same guard via a Playwright `page.route` handler — a
    single pre-navigation check alone is insufficient for a real browser. DNS rebinding is a named,
    undefended residual limitation, not silently assumed away.
  - `headed=False` by default (a one-shot CLI call from an agent's shell tool usually has no
    display attached); `--headed` is for a human operator co-located with a real display who wants
    to watch — it changes nothing about what the function itself returns.
  - `agentteams/frameworks/goose.py`'s `_goose_capabilities_content()` and
    `agentteams/templates/universal/skill-generation.reference.template.md` (new "Worked example: a
    page `fetch` can't render" section) both document the tiered escalation path (static
    fetch → `agentteams.research.browser`, if installed → durable-infrastructure gap otherwise),
    using verify-first phrasing rather than asserting the capability is present for any given team.
  - Full design/audit trail: `tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md`.

### added (AI bad-habits catalog: BH-11 utility-call model inheritance)

- **New catalog entry `BH-11`** (category: AI-specific correctness, cross-linked to `CH-23`):
  *"A backstage/utility LLM call silently inherits a user-facing, dynamically-selectable model (or
  other generation parameter) choice, without accounting for that model's behavioral differences."*
  Surfaced while investigating a LingoFriend production bug (weather/news questions silently
  returning no live-research grounding whenever a "thinking" chat model was selected) — the
  underlying failure mode generalizes to any agentic system with more than one LLM call per turn
  and a user-selectable model, so it's cataloged here for `@code-hygiene` (CH-25) to catch in every
  current and future agentteams-scaffolded project, not just the one that surfaced it.
  `references/ai-bad-habits-watch.md` regenerated from the updated catalog.

### fixed (bridge subagent stubs absolute-path leak)

- **Claude and Goose subagent bridge stubs no longer embed the operator's absolute filesystem
  path.** Found by dogfooding `--update --merge` for the `claude` framework against two real
  repositories: every bridged stub for a hand-authored specialist agent (and, historically, some
  core governance agents) carried a `- Source absolute path: /Users/<name>/...` line generated by
  `agentteams/bridge_subagents.py`/`bridge_subagents_goose.py`. The line was redundant — the stub
  already states the source location as a repo-relative path (in YAML front matter and in the
  body) two lines earlier — so it's removed rather than sanitized in place; Claude/Goose resolve
  the relative path against the repository root at read time regardless. Two new regression tests
  pin the never-absolute contract for both bridge targets. Pre-existing stubs already committed
  with the old line are unaffected by this fix (their content predates it and is preserved by the
  merge shrink-guard, as designed); only newly-generated or regenerated stubs are clean.

### added (Post-Deliverable Retrospective)

- **New `@orchestrator` subroutine, "Post-Deliverable Retrospective,"** runs after a primary
  deliverable is produced or revised (Workflow 1, Workflow 2, and Workflow 3's corrections-made
  branch — not Workflow 4, which would double-count already-retrospected deliverables) and before
  Standard Doc-Sync Closeout. It enumerates two audited lists: generalizable lessons about the
  current project's own agent infrastructure (applied in-repo via `@agent-updater`), and
  remediation items for the AgentTeamsModule tool itself (logged to
  `references/agentteams-remediation-log.csv` via a new `@repo-liaison` Protocol 5, so they
  survive past the session that surfaced them). Both lists are challenged by `@adversarial` and
  `@conflict-auditor` before anything is applied or logged; the conflict-auditor step also rejects
  or sanitizes any item that reads as formula-injection or credential-like content. An empty
  retrospective is the expected common case and costs one no-op line. Full semantics (category
  definitions, dedup rule, CSV schema, and an explicit self-referential destination exception for
  AgentTeamsModule's own gitignored dogfood output directory) live in the new
  `references/retrospective-remediation.reference.md`. New `agentteams/liaison_logs.py` CSV
  constant (`AGENTTEAMS_REMEDIATION_CSV`) wired into the existing `init_csv_stubs()` mechanism —
  no call-site changes needed.

### fixed (delivery-receipt absolute-path leak)

- **`_write_delivery_receipt()` no longer writes an absolute filesystem path (embedding the
  operator's OS username) into `references/delivery-receipt.json`.** Found by `@security` during
  the Post-Deliverable Retrospective work above: regenerating this repo's own checked-in example
  snapshots (`examples/*/expected/`) via `agentteams --update`/`--overwrite` wrote the real local
  absolute path into a file headed straight for a tracked, published fixture. The schema already
  documented `output_dir` as "absolute or repo-relative... informational only," so the fix needed
  no schema change: a new `_sanitized_output_dir()` helper in `agentteams/cli/artifacts.py` returns
  a repo-relative path when the output directory is inside a git repository (walking up for a
  `.git` marker), or just the directory's own name otherwise — never an absolute path. The leak
  never reached git history (caught pre-commit); the 4 affected example receipts were regenerated
  clean. Two new regression tests in `tests/test_delivery_receipt.py` pin the never-absolute
  contract for both branches (inside/outside a git repo).

### fixed (test harness)

- **`tests/test_integration.py::test_snapshot_comparison` no longer flags
  `references/framework-watch.reference.md` as a false-positive diff.** This file carries live
  framework-research fetch data (timestamps, diff summaries), exactly like the two files already
  excluded as non-deterministic (`security-vulnerability-watch.reference.md`, `security.agent.md`)
  — it was simply missing from that same `_live_data_files` exclusion set. Unrelated to the
  Post-Deliverable Retrospective feature above; found while regenerating the example snapshots for
  it and fixed in the same pass since it blocked a clean test run.

### fixed (security-refs offline/stale-cache round-trip)

- **`agentteams.security_refs.build_security_placeholders()` no longer degrades a real cached CVE
  watch to `UNKNOWN-CVE`/blank placeholders on `--security-offline` or a live-fetch-failed fallback**
  — the write path persisted vulnerability records in one key-naming scheme (`cve`/`vendor`/`name`/
  `date_added`/...) while the render/rebuild code assumed the live CISA-KEV-API scheme (`cveID`/
  `vendorProject`/`vulnerabilityName`/`dateAdded`/...); every `.get(liveKey, default)` silently fell
  through on a cache read, and — worse than the original symptom — the corrupted (blanked) records
  got written straight back to `security-vulnerability-watch.json` on that same run, compounding
  across subsequent runs. Root-caused and reported from `visualknowledge/collector-management`
  (2026-07-22 incident; no data was actually lost there — the operator's agent self-caught it and
  restored from `agentteams`'s own automatic pre-write backup — but the underlying bug was real,
  general, and reproduces for any consumer repo on any `--security-offline` run against a real prior
  cache). Fixed by normalizing every KEV record to one canonical shape immediately after fetch (or
  reading it back unchanged from cache, since that's the only shape ever persisted), with enrichment
  (EPSS/CVSS) merged inline per-record rather than via separate lookup maps that never survived an
  offline read. A resilience guard also drops any cached record with a blank `cve` (protects against
  a cache already degraded by a pre-fix run) instead of rendering it. The two existing cache-fixture
  tests were themselves testing a cache shape that could never exist in the wild (live-API-shape
  keys — this codebase's write path has never persisted that shape) — fixed to use the real shape,
  and a new round-trip test writes a cache via the actual online code path, then reads it back
  offline, closing the coverage gap that let this ship silently. See
  `references/plans/security-offline-cache-schema-mismatch.report.md` (incident) and
  `references/plans/security-vuln-cache-normalization.plan.md` (fix).
- **New `schemas/security-vulnerability-watch.schema.json`** formalizes the canonical vulnerability-
  record shape (Draft-07, `additionalProperties: false` on `vulnerabilities[]`/`osv_packages[]`
  items) and is now validated on both write (fail-open: warns, never blocks a real `--update`) and
  read (a cache that fails validation is rejected — with a `UserWarning` naming the violation — and
  treated as absent rather than trusted). This is the recurrence-prevention layer: a future
  write/read shape drift gets its own cache rejected visibly instead of silently defaulting every
  field again. See `references/plans/security-vuln-cache-json-schema.plan.md`.

### changed

- **`agentteams/bridge.py`'s goose-target `.goose/README.md` now points to its own
  `quickstart-snippet.md`** instead of a single unadorned sentence ("Lightweight bridge; source
  files are canonical."), so a Goose developer reading the README first isn't left without a pointer
  to operational guidance (retrieval-first protocol, `--bridge-check` scope, MCP wiring). Fence
  bumped `goose-bridge-readme` v1→v2. Scoped to the goose call site only — the byte-identical claude
  bridge README literal is untouched (separate, unlogged finding, not part of this fix). Remediates
  this repo's own `.github/agents/references/conflict-log.csv` row (gitignored; dated 2026-06-22,
  found stale-but-still-valid while auditing that log's 5 open `copilot-vscode-to-goose` rows — the
  other 4 were either already fixed by an earlier commit or don't have a proportionate fix available;
  see `tmp/by-week/2026-W29/goose-bridge-conflict-log-remediation.plan.md`). No test asserted the
  prior exact wording (confirmed by grep before changing it).
- **`technical-validator.template.md`'s Accuracy Rules renamed `CH-01`..`CH-07` → `TV-01`..`TV-07`,
  closing a pre-existing `CH-`-prefix collision** with the unrelated, much larger `CH-01`..`CH-28`
  catalog in `code-hygiene.template.md`/`code-hygiene-rules-reference.template.md` (e.g. "CH-02"
  meant "file paths must resolve" under one template and "Script Lifecycle" under the other) —
  flagged but explicitly deferred by the LingoFriend-handoff audit above as a separate finding.
  Rule text is unchanged; codes only. Confirmed via full-repo grep before renaming: every other
  `CH-0X` reference in the codebase (`build_team.py` and other module docstrings,
  `agentteams/ai_bad_habits.py`'s `cross_links`, `tests/test_code_hygiene.py`,
  `tests/test_ai_bad_habits.py`, `unix-philosophy-mapping-reference.template.md`,
  `docs_src/api-reference/*`) resolves to code-hygiene's catalog, never technical-validator's — pure
  prompt text, nothing machine-parses these strings by value. `TV-` confirmed unused elsewhere.
  Regenerated the 3 test-covered example snapshots (`software-project`, `research-project`,
  `data-pipeline`). Full plan: `tmp/by-week/2026-W29/technical-validator-ch-rename.plan.md`.
  **Deliberately not touched by this change** (each independently confirmed, not merely assumed):
  the self-hosted `.github/agents/technical-validator.agent.md` — a `--self --update --merge
  --dry-run` confirmed the tool detects the template changed but correctly classifies the file
  `UNCHANGED`, since the Accuracy Rules table is a USER-EDITABLE section and merge mode preserves
  already-generated USER-EDITABLE content by design; forcing it current would need a full
  security-cleared `--overwrite`, disproportionate for a gitignored, non-shipped local artifact.
  `examples/project-repositories/` — excluded from every pipeline-integration test's parametrize
  list and found, on independent review, to be 11 files and seven weeks stale (missing
  `style-guardian.agent.md` entirely, missing a Code-index-consultation feature) for reasons
  unrelated to this rename; flagged as its own separate resync task rather than folded in here.

- **Four canonical-module process gaps closed after auditing a downstream project's (LingoFriend)
  real remediation incident** (basis: that project's `workSummaries/daily/2026-07-21.md` and
  `docs/news-retrieval-enumeration-failure-report.md`, handed off as
  `lingofriend-agentteams-module-handoff.plan.md` and re-audited against this repo's actual
  template text before adoption). All changes are additive prose within existing FENCED/
  USER-EDITABLE sections — no new agent, tool grant, or slot. **(A)** `orchestrator.template.md`
  Workflow 0 step 2 and `adversarial.template.md`'s "Environmental assumptions" bullet now name
  composition-root/harness fidelity for live reproductions: a test harness that silently exercises
  a stand-in wiring instead of the real production entry point can make a negative result ("zero
  output") indistinguishable from a genuinely reproduced bug. **(B)** `conflict-auditor.template.md`'s
  `SOURCE_DRIFT` category and `conflict-resolution.template.md`'s matching decision rule now
  explicitly cover a forward citation — a plan/report path that was never written — not only
  staleness against a since-changed file; `technical-validator.template.md`'s CH-02 now covers file
  paths in source-code comments/docs/reports, not only deliverables. **(D)** Workflow 11 Part B's
  repo-at-large closeout scan adds `{CONFLICT_LOG_PATH}` as a fourth issue source; unlike the other
  three (summarized and presented only), a conflict-log row found `open` with no `resolution` now
  gets a real `@conflict-resolution` ACCEPT/REJECT/REVISE decision, since "was this actually fixed
  already" has a concrete, checkable answer at closeout time. **Deliberately not adopted:** a
  same-review proposal to also add a plan/report-path grep recipe to
  `code-hygiene-rules-reference.template.md` — already fully covered by CH-02 and `SOURCE_DRIFT`
  above, and a third, differently-numbered `CH-` entry for the same concern would worsen the
  pre-existing `CH-`-prefix collision between `technical-validator.template.md` (CH-01..CH-07) and
  `code-hygiene.template.md` (CH-01..CH-28) — a known, separately-tracked naming-hygiene item, not
  part of this change.
- **`orchestrator.template.md`'s Pre-Execution Requirement now recommends generating/editing
  `.steps.csv`/`{CONFLICT_LOG_PATH}` rows programmatically and re-parsing any hand-edited row with
  `agentteams.plan_steps.read_steps()` before trusting it** — an unquoted embedded comma or a stray
  quote can silently shift every subsequent column in a way visual inspection won't reliably catch.

### fixed

- **`agentteams.plan_steps.read_steps()` no longer lets a column-overflow row leak a stray `None`
  key (mapped to a list, not a `str`) into its returned `dict[str, str]` rows** — already a
  violation of this function's own documented return contract
  (`docs_src/api-reference/plan_steps.md`). A row with more comma-separated values than the header
  (typically an unquoted comma or stray quote in a hand-edited cell) now emits a `UserWarning`
  naming the file and physical line number instead of silently discarding the signal. Confirmed
  against this repo's own gitignored `tmp/by-week/**/*.steps.csv` history, which already carries
  roughly two dozen such rows predating this fix — the reason this warns rather than raises: raising would
  break on real existing data the moment anything re-parses it. New coverage:
  `tests/test_handoff_payloads.py::test_steps_reader_warns_on_column_overflow`.

### added (code-as-agent-harness audit)

Basis: `references/plans/code-as-agent-harness-audit.report.md`, an audit of `agentteams/` against
["Code as Agent Harness"](https://arxiv.org/abs/2605.18747) — code increasingly serves as the
operational substrate for agent reasoning, action, and verification, not just a target output.
Three value-adding gaps were identified, each with its own audited plan
(`references/plans/code-as-harness-*.plan.md`) and implemented; one candidate (converting the
conflict-code routing tables in `conflict-auditor`/`conflict-resolution` templates to a
`mcp_detect.py`-style pure function) was considered and explicitly rejected — applying those rules
still requires reading files and forming a judgment, so a function would only replace a dict lookup
a markdown table already serves adequately.

- **`agentteams.memory_index.query_index()` / `agentteams.code_index.query_partition()` /
  `query_partitions()` now return a computed `confidence` field** (`"reliable"` / `"candidate"` /
  `"weak"`) per hit, alongside the existing raw `score`. Replaces threshold-interpretation prose
  ("top-1 ≥ 3.0 is reliable; 1.0–3.0 is candidate...") that had been copy-pasted, and had already
  drifted, across 6 templates (`conflict-resolution`, `conflict-auditor`, `quality-auditor`,
  `research-analyst`, `retrieval-integrator`, `tool-doc-researcher`) — `tool-doc-researcher` was
  missing the vector-strategy fallback the other five carried; all six now cite the code-computed
  field with a manual-threshold fallback for text-only runtimes, mirroring the existing
  `handoff_payloads`/`behavioral_drift` documented-dotted-path convention. `code_index.py` keeps its
  own independent copy of the threshold logic per the module's existing R2-M3 no-import-from-
  memory_index invariant; the scoring-parity tests now also assert `confidence` parity between the
  two. `agentteams --query-index`/`--query-code` CLI output prints `confidence=` alongside `score=`.
  New coverage: `tests/test_memory_index.py`, `tests/test_code_index.py`.
- **`agentteams.scan` gains `verdict_for_findings()` (HALT/CONDITIONAL_PASS/PASS) and a
  `python -m agentteams.scan <path>` entrypoint** (mirrors `agentteams.research`'s `__main__`
  rationale — a runtime with shell/`execute` access but no way to natively `import` and call
  `scan_content` directly). Closes a real gap: `scan.py`'s deterministic PII/credential/machine-
  info scanners were wired into exactly one call site (`--scan-security`, generation-time only,
  against already-emitted `.agent.md` files) — nothing let `@security` invoke them at review time
  against arbitrary deliverable/diff content, so Rules S-1 and S-8 in `security.template.md` asked
  the agent to re-derive by eye exactly what `scan.py` already detects by regex. Both rules now cite
  `agentteams.scan.scan_content(text)` (or the new CLI) as the preferred check, with the existing
  manual-pattern bullets retained as fallback — no tool-grant change (`@security` stays
  `['read', 'search']`; the citation follows the same invoke-if-available convention already used
  for `handoff_payloads`/`behavioral_drift`). The HALT-table's Credential/Machine-specific-info rows
  now cross-reference `verdict_for_findings()` as their scan-derivable subset — the remaining rows
  (destructive-op confirmation, external writes, injection attempts) stay procedural judgment calls,
  not mechanized. New coverage: `tests/test_scan.py`.
- **New `agentteams.session_scan` module + `python -m agentteams.session_scan [repo_root]`
  entrypoint** consolidates three of the four issue sources `orchestrator.template.md` Workflow 11
  Part B ("Repo At-Large Issues") step 1 described as independent hand-run greps —
  `CHANGELOG.md` "Known Issues" heading, pending/blocked rows in the gitignored `tmp/**/*.steps.csv`
  tree (via `agentteams.plan_steps.read_steps()`), `git status --short` anomalies — into one
  `scan_repo_issues()` call returning structured `RepoIssue` records. The fourth source,
  `{CONFLICT_LOG_PATH}`, is intentionally NOT covered — Part B step 2 already routes it through
  `@conflict-resolution`'s ACCEPT/REJECT/REVISE decision (a judgment call, not a summarize job) per
  the in-flight LingoFriend-handoff remediation above; folding it into a generic scanner would
  regress that. `_scan_git_status` takes an injectable `runner` (mirrors `pr_management.py`'s
  `_run_gh`/`GhRunner` testability shape) and never shells with `shell=True`. New coverage:
  `tests/test_session_scan.py`. Dogfooded via `python build_team.py --self --update --merge`
  against this repo: convergent after one merge pass, post-audit clean, no tracked file touched.

### added

- **`agentteams[research]` — a real, optional runtime library (web search, curated-source rating,
  dual-lens claim verification), not a design-time template.** A disclosed, bounded exception to
  the "generator, not a runtime" boundary (see `SECURITY.md`): `pip install agentteams[research]`
  installs `agentteams.research` (`search`/`reputable`/`verify`), a small, self-contained Python
  library with zero import-time coupling to the CLI/generator pipeline in either direction and
  zero effect on the base install's dependency footprint (still `jsonschema` only). Includes a new
  `research-analyst` domain-archetype template (opt-in only, gated on an explicit `capabilities:
  ["research_verification"]` brief field, never inferred) documenting how an agent orchestrates
  it, and a `python -m agentteams.research search|fetch` CLI for the two calls that need no chat
  backend. Full API reference: `docs_src/api-reference/research.md`. Ported and generalized from a
  working pattern in a downstream consumer project (LingoFriend) — every consumer supplies its own
  domain-allowlist data via `AllowlistConfig`; none is hardcoded into the library. **Note on
  timing:** this lands during the `1.0.0rc6` pre-release soak, which `STABILITY.md` describes as
  accepting only bugfix changes; landing anyway is consistent with actual recent practice on this
  branch — `de1ce5a` (itself `feat`-tagged during the same rc) is one of roughly a dozen further
  `feat:`-tagged commits already landed since the rc.6 cut, and this section already held several
  other feature-shaped entries before this one — rather than a new departure; flagged explicitly
  here rather than silently. **Note on `mkdocs build --strict`:** it does not pass on this branch,
  but that's pre-existing and unrelated — confirmed by running the identical build against a clean
  `origin/main` checkout, which fails with the exact same 3 warnings (dead links in `changelog.md`
  and `verification-environment.md`, none touching this change). `research.md`/`mkdocs.yml`
  themselves introduce zero additional warnings, which is the bar the equivalent situation was
  already held to for `runtime-security-guide.md`/`goose-runtime-pattern-guide.md` in
  `references/plans/lingofriend-handoffs-metaplan-2026-07-18.plan.md`.
- **`agentteams.research.news` — news as perspective, not fact.** The `type="news"` tag
  `reputable.py` already carried (11+ domains once a consumer supplies its own `AllowlistConfig`,
  3 in `DEFAULT_CONFIG`) was inert — stored and returned but never read for behavior. New module
  `news.py` gives it real behavior: `is_news_source()`, and `perspective_attribution()`, a
  single shared attribution-string formatter so a consumer presents a news claim as a
  contemporaneous account ("outlet reported, as of date"), not settled fact. A `PerspectiveKind`
  alias (`"reported"` / `"contested"`) distinguishes a plain factual report from a claim about how
  a source *characterized* something — the latter warranting more hedging, a distinction lifted
  from a proven pattern at a downstream research consumer project. Deliberately never uses the
  word "primary" for this concept — `tier="primary"` already means something else (a repository of
  original source *texts*). `search.py` gains `extract_published_date()` (best-effort, regex-based
  — JSON-LD, `article:published_time`/`date`/`pubdate` meta tags, `<time datetime>` — never
  fabricates, honest `None` on no match) and additive `fetch_text_and_date()` (one fetch, both
  text and date; `fetch_text()`'s own signature/behavior unchanged). Full API reference:
  `docs_src/api-reference/research.md`.

### fixed

- **Code index (F-CODEIDX) is now surfaced to the *primary* agent team, not only the
  Claude bridge skill.** `/code-recall` was emitted solely into `.claude/skills/`, so
  Copilot-framework teams (which have no skills concept — retrieval awareness lives in
  agent templates) never learned about `--query-code`. Added a fenced
  `code_index_consultation` section to the **navigator** and **retrieval-integrator**
  templates (parallel to `memory_index_consultation`), so every framework's rendered
  agent team surfaces the code-index retrieval protocol. Because the section is fenced,
  `--update --merge` propagates it into existing teams. New guard
  `tests/test_template_code_index_consultation.py` asserts a fresh Copilot team renders
  `--query-code` into its agent files (not only the Claude skill).

### added

- **CH-29 — Script-First Output Discipline: Build and Reuse Reference Files** (new
  code-hygiene catalog rule). Before producing non-trivial output, an agent should ask
  whether a script/program would produce it more effectively than manual work; if so,
  enumerate the languages/libraries needed, build the script, and create/update a
  `references/ref-<library>-reference.md` file per language/library used so the same
  research is reused on the current and any similar future task. Extends (does not
  duplicate) [[CH-27]] (recurring-logic promotion, judged by foreseeable recurrence)
  and the existing `@tool-doc-researcher`/`detect_reference_tools()` tool-reference
  system (which only fires for tools pre-declared in `.agentteams/brief.json`) — CH-29
  fires generically at output-production time regardless of recurrence or
  pre-declaration. Added to
  `agentteams/templates/domain/code-hygiene-rules-reference.template.md`, cross-referenced
  in `unix-philosophy-mapping-reference.template.md`, and wired as an explicit step into
  `primary-producer.template.md`'s Production Workflow — the one domain agent always
  included in every generated team regardless of project archetype — so it propagates to
  new teams and, via `--update --merge`, to existing ones. New regression tests in
  `tests/test_agent_feature_wiring.py` guard the wiring.

- **Runtime-governance guidance for generated apps that serve LLM output (docs-only;
  scope boundary + two new guides).** Building the LingoFriend project (an agentteams-generated
  team) surfaced a class of concern that lives in a *produced application's runtime*, not in the
  design-time team — which agentteams, being "a generator, not a runtime," does not cover. This
  release documents that boundary rather than expanding scope into runtime code:
  a **Design-time vs runtime governance** note in `SECURITY.md` and `README.md`; a new
  **"Runtime Security for Served Apps"** guide (`docs_src/runtime-security-guide.md` — output-safety
  gate as a *floor* not moderation, applying "data-not-instructions" to a served app's own runtime
  prompts, input sanitization + bounds); and a new **"Per-User Runtime Goose Pattern"** guide
  (`docs_src/goose-runtime-pattern-guide.md` — per-user named-session isolation, and a binding rule
  to keep latency-critical/interactive paths *off* `goose run`). Both guides are registered in the
  MkDocs nav and carry LingoFriend reference implementations as illustrative *floors*, not solutions.
  Docs only — no CLI flags, schemas, or emitted artifacts changed. Ref: LingoFriend handoff
  `agentteams-runtime-governance` (AT-1/AT-2/AT-3).
- **Curated OS security-hardening references (Linux, macOS, Windows) that `@security`
  consults for platform targets.** Three new rendered reference templates —
  `references/security-{linux,macos,windows}-hardening.reference.md` — give the agent a
  platform-hardening baseline (the systems-tier companion to the low-level *code*
  screening block). All three share **one identical set of 10 domain titles** (parallel
  structure, CI-enforced): system integrity/kernel/secure boot; privilege escalation;
  mandatory access / application control; application isolation, sandboxing & containers;
  capability & process-mitigation restriction; userspace memory-protection; service/daemon
  hardening; filesystem/disk-encryption/secrets; auditing/compliance; and vulnerability
  catalogs — every source a **web-verified primary authority** (kernel.org & Linux man-pages;
  Apple Platform Security & Developer; Microsoft Learn & MSRC; NSA/CISA, NIST/MITRE, CIS,
  OpenSSF), plus landmark exploit-class CVEs (e.g. Dirty Pipe, runc escape, PwnKit;
  Shrootless, powerdir; PrintNightmare, Zerologon). The security agent template gains an
  **OS-gated, directionless pointer** inside the `security_rules_invariant` fence (applies
  only to the matching deployment OS), so `--update --merge` propagates it into existing
  teams. Registered in `output_plan.py`; new guard `tests/test_security_platform_hardening.py`.
  Sources verified per Rule S-3 (Reference Integrity) — no fabricated links. **Audit-revised
  (2026-07-09)** via adversarial + conflict audits: normalized the domain titles to hold the
  parallel-structure claim, removed a non-primary man-page mirror, corrected CWE-787 naming
  (out-of-bounds write) and a Smart-App-Control/arm64e overclaim; see
  `references/plans/security-hardening-wave-audit.{report,plan}.md`.
- **`@security` agent now screens for low-level / systems vulnerabilities, in any
  language.** The security template's screening taxonomy was exclusively web/LLM-tier
  (XSS/SQLi/CSRF/broken-access-control + slopsquatting + unsanitized-output-to-sink +
  the OWASP LLM Top 10). Added a fenced **"Low-Level & Systems Vulnerabilities (Any
  Language)"** block to `security_rules_invariant` covering three tiers,
  proportionately: (1) **arbitrary-code-execution / injection sinks** — command
  injection (CWE-78), `eval`/`exec` code injection (CWE-94/95), unsafe deserialization
  (CWE-502), path traversal (CWE-22), SSRF (CWE-918), XXE (CWE-611), unsafe reflection
  (CWE-470), insecure temp files (CWE-377) — applied to any language; (2) **memory-safety
  corruption** — out-of-bounds write/overflow (CWE-787/120/121/122), OOB read (CWE-125),
  use-after-free (CWE-416/415), integer overflow/underflow (CWE-190/191), format string (CWE-134), type confusion
  (CWE-843), TOCTOU (CWE-367) — surface-gated to native/unsafe code (C/C++/Rust `unsafe`/
  cgo/FFI/inline asm); (3) **hardware/microarchitectural** — an honest *awareness +
  candidate-flag* posture (constant-time / Spectre-v1 gadget, CWE-208) with an explicit
  scope boundary: full Spectre/Meltdown/Rowhammer analysis is out of scope for per-line
  LLM review and routes to specialist tooling. Also adds a Mandatory-Review-Triggers row
  for AI-authored native/unsafe-memory changes, a static **MITRE CWE** threat-intel source,
  and control **CTRL-11**. Because the block is fenced, `--update --merge` propagates it
  into existing teams. New guard `tests/test_security_lowlevel_coverage.py`. Ownership
  boundary with `@code-hygiene` preserved (these are exploitable → `@security`'s).
  Analysis: `references/plans/security-low-level-vuln-coverage.{report,plan}.md`.
- **Code & API vector index (F-CODEIDX) — a searchable, auto-refreshed retrieval
  cache over repository scripts and the external APIs they use.** The
  code-retrieval sibling of the memory index (F8): where the memory index covers
  durable prose, this covers *code*. New module `agentteams/code_index.py` +
  `schemas/code-index.schema.json` build a stdlib-only **sparse tf·idf
  vector-space** index (BM25 lexical + cosine vector strategies) with a
  code-aware tokenizer (keeps short identifiers like `os`/`re`, keeps dotted
  import paths whole *and* split, splits `snake_case`/`camelCase`) and AST-based
  symbol-aware passages.
  - **Labeled by `source_kind`** — `local-script` (repository scripts),
    `api-module` (external API source the scripts import), `api-doc` (API
    documentation) — filterable at query time with `--code-kind {local,api,doc,all}`.
  - **Gitignored local cache** under `references/code-index/` (`manifest.json` +
    per-kind partition files); never committed, never drift-tracked, never staged
    by a git hook. The BM25/cosine scorers are a parity-tested copy of the
    memory-index's (the shipped, grid-tuned module is left untouched).
  - **New CLI:** `--refresh-code-index`, `--query-code TEXT` (auto-refreshes a
    stale partition first), `--code-query-k`, `--code-query-strategy
    {lexical,vector}` (default `lexical`), `--code-kind`.
  - **Auto-update triggers:** query-time staleness (primary) — the `local`
    partition on source hash/mtime, the `api-*` partitions on a dependency
    fingerprint (dependency-manifest contents + import-name set + dist→version) so
    a dependency upgrade is detected even though no local file changed; plus
    `--update` (keeps an existing cache fresh) and an optional off-by-default
    pre-commit warm-up.
  - Never executes third-party code (static `ast` + `importlib.metadata` only).
    Atomic writes; empty-repo ⇒ empty-but-valid; non-blocking fallback to file
    read then grep. Docs: `docs_src/api-reference/code-index.md`. Audited design
    (two adversarial + conflict rounds): `references/plans/code-api-vector-index.plan.md`.

- **Parallel execution of independent plan steps (Workflow 0A).** Every team
  agentteams creates or `--update --merge`-updates now inherently identifies plan
  steps whose domains are independent and dispatches them as parallel **waves**
  instead of strictly one at a time — under a conservative, fail-safe heuristic.
  - New module `agentteams/parallel_plan.py` + CLI
    (`python -m agentteams.parallel_plan <steps.csv> [...] [--json]`): reads the
    runtime-schema plan-steps CSV with an **optional, additive `depends_on`
    column**, builds the dependency DAG (declared edges plus footprint-implied
    read-after-write / write-write edges), detects cycles as blocking errors, and
    emits ordered waves. Independence requires disjoint read **and** write
    footprints (path equality or directory/file containment) and no contact with a
    shared-mutable-state denylist (git, databases, locks, network, servers,
    migrations); steps with empty/unparseable footprints fail safe to singletons.
    Also reports cross-plan *any-order* (non-blocking) sets — a scheduling note,
    not simultaneous execution.
  - The orchestrator gains, inside the merge-propagated `available_workflows`
    fence: **Workflow 0A (Parallelization Analysis)** (per-wave dispatch with
    `@conflict-auditor` run per member at wave join — preserving the per-step
    effect-audit guarantee — and `@adversarial` once per wave; destructive /
    cross-repo / `--bridge-refresh` steps forced to singleton waves with full
    per-step clearance), an optional `depends_on` in the Pre-Execution
    Requirement, a recurring cross-plan independence scan in Workflow 10, and a
    routing row. A non-load-bearing constitutional summary (Rule 16) is added for
    new teams.
  - Always-emitted `references/parallelization.reference.md` documents the
    contract for **all** frameworks; a `parallelize-plan` Claude **skill** is
    emitted via the bridge (copilot-vscode→claude) gated by a new
    `parallelize` host feature. The optional column is **backward compatible**: a
    7-column CSV stays valid and is treated conservatively. Report, plan, and
    adversarial+conflict audits under
    `references/plans/parallel-independent-plan-execution.report.md`.
- **Stale detector/remediator (`--stale-check`).** Additive minor change (new flags +
  the exit-3 contract enter the SemVer surface per `STABILITY.md`). A standalone,
  read-only scan of `--output`/`--project` (else CWD) that reports stale agent docs and
  code/scripts across reliability tiers:
  - **Tier-1 (blocking, exit 1):** `VCS_CONFLICT_MARKER` (a complete, ordered git
    merge-conflict triad, fenced-code and setext-underline aware), `BROKEN_REF` (a
    markdown-link target absent on disk), and provenance-gated `INTEGRITY` (reuses
    `drift.verify_output_integrity` for every discovered `references/build-log.json`)
    and `SOURCE_DRIFT` (bridge source divergence, gated on source presence).
  - **Tier-2 (advisory):** `STALE_VS_CODE` — git-recency: referenced code committed in a
    commit strictly *after* the doc's last commit, with a substantive (whitespace-filtered)
    diff. Self-disables on non-git/shallow repos and skips uncommitted paths.
  - **Tier-0 (INFO):** `PROVENANCE_ABSENT`, `BRIDGE_SOURCE_UNAVAILABLE` (a consumer repo
    whose bridge `source_dir` is not present on this machine).
  - The file set comes from `git ls-files` in a work-tree (excludes gitignored `tmp/`,
    backups, local `references/plans/`) plus an explicit `examples/` fixture skip.
  - `--stale-remediate` prints a **guided remediation plan**; adding `--yes` promotes it
    into an **applied, backup-protected revision pass**: it takes a sha256-verified safety
    snapshot of every file it will touch into `.agentteams-backups/stale-fix-<ts>/` BEFORE
    writing, then performs only safe deterministic revisions — broken-reference repair
    (relocating a link to a moved file, never inside a fenced/USER-EDITABLE region) and
    bridge re-merge for `SOURCE_DRIFT` (the canonical fence-aware writer). `INTEGRITY` is
    routed (the exact `--update --merge` command is printed, not auto-run); conflict markers
    are never auto-resolved. Exit 3 when blocking items remain after an apply.
  - `--stale-restore [TS]` recovers files from a stale-fix snapshot (default: latest),
    verifying each backup's sha256 before writing — the recovery path for a revision that
    went wrong. `--stale-no-git` skips the Tier-2 git-recency signal. The man page documents
    exit 1/2/3. Methods report, plan, and adversarial+conflict audits under
    `references/plans/stale-detector-*`.
  - **`.agentteams-stale-ignore`** (gitignore-style file at the scan root) suppresses
    known-acceptable findings — cross-repo links, captured/read-only packages, archival
    docs. Matches the referrer file or (for a broken ref) the target; supports exact paths,
    directory prefixes, and `*` globs. **Never suppresses `VCS_CONFLICT_MARKER`.** Suppressed
    findings are counted (`suppressed: N`), never silent; note suppression can flip a Tier-1
    exit `1` to `0`. (Evolvable input config, not a frozen format.)
  - The revision phase now **warns** when a safety snapshot is written into a repo where
    `.agentteams-backups/` is not gitignored (it never auto-edits `.gitignore`).
  - The write/revision phase moved to a new module `agentteams/stale_remediate.py`
    (`stale_detector.py` stays detection-only — CH-07 module-size discipline).

- **Goose (Block / AAIF) framework support (beta).** Adds Goose as a target
  alongside Copilot and Claude. **Beta:** generate/convert/bridge are supported and
  validated against the Goose CLI; interop-to-Goose is not yet supported and the
  `goose` adapter API is not yet covered by the stability policy (`STABILITY.md`).
  - **Generate** (`--framework goose`): emits one Goose recipe per agent under
    `.goose/recipes/<slug>.yaml`, an `orchestrator.yaml` that delegates to specialist
    recipes via `sub_recipes` (deeper handoff edges become `summon` `load(...)`
    references — Goose forbids nested delegation), the team brief as a repo-root
    `AGENTS.md` plus a `.goosehints` integrator, and a runnable `team-builder.yaml`
    recipe. Handoffs are encoded natively in the recipes (no `runtime-handoffs.json`
    sidecar). All emitted recipes pass `goose recipe validate`.
  - **Bridge target** (`copilot-vscode|copilot-cli|claude → goose`): writes fenced
    `AGENTTEAMS-BRIDGE` pointer files (`AGENTS.md`, `.goosehints`, `.goose/README.md`)
    so a Goose project reuses canonical source agents without regeneration. `AGENTS.md`
    is a shared multi-tool file — `--bridge-merge` updates only the fenced region and
    preserves an existing unfenced file; `--bridge-refresh` overwrites it (see
    `references/bridge-refresh-safety.md`).
  - Convert/interop **to** Goose are not yet supported (deferred).

- **Automatic past-day work-summary backfill + trigger.** Promotes the
  collector-management-developed feature into the framework: every generated team
  now gets a session-close sweep that fills *recent past active-day* daily-summary
  gaps automatically (in addition to the existing today-capture). Three coordinated
  pieces, with all semantics defined **once**:
  - **New reference `references/work-summary-backfill.reference.md`** (universal
    template, emitted for every team) — the single source of truth: the
    `AUTO_BACKFILL_LOOKBACK_CAP_DAYS` named constant (default 14), canonical clock
    (git author-date, repo-local TZ), the disjoint+exhaustive Rule-12 partition
    (today vs strictly-prior), executed-work trigger gate, create-only scope,
    honor-prior-skip fail-safe, idempotency/no-recursion, recommend-only overflow
    forcing function, and the mandatory adversarial→conflict audit gate, plus the
    request-driven Steps 1–7 (gap analysis, date attribution, evidence model,
    validation, audit).
  - **`@work-summarizer` Workflow D — Automatic Backfill Sweep** — implements the
    obligation; defers all semantics to the reference; daily-only override of the
    request-gated weekly/monthly gap-fill; runs at most once per session, no
    recursion.
  - **Orchestrator Past-Day Backfill Obligation (Constitutional Rule) + closeout
    step 8** — at session close, when the session executed work, detect past
    active-day gaps and invoke Workflow D (strictly-prior dates only; disjoint
    from the Rule 12 today-capture). Step 8 is inside the `available_workflows`
    fence so it propagates on `--update --merge`; the rule + Workflow D live in
    unfenced regions (like Rule 12 / the Daily-Weekly-Monthly workflows) and
    reach existing teams on a fresh render. Tests in
    `tests/test_work_summary_backfill.py`; plan + adversarial/conflict audits under
    `references/plans/work-summary-backfill-integration-2026-06-15.plan.md`.

- **Data-safety hardening for the output tree — atomic writes, integrity
  verification, and bounded + mirrored backups.** Three coordinated capabilities
  that make generated output recoverable and tamper-evident:
  - **Atomic agent-file writes.** Generated agent files, their sidecars, and
    `--restore-backup` now write to a temp file and `os.replace` it into place, so
    an interrupted run can never leave a half-written or truncated agent file.
  - **Integrity verification (read-only).** `--verify-integrity` classifies every
    generated file against the build-log `file_hashes` baseline as `OK` /
    `MODIFIED` / `TRUNCATED` / `MISSING` / `FENCE-BROKEN`, exiting non-zero on
    `TRUNCATED`/`MISSING`/`FENCE-BROKEN` (`MODIFIED` is advisory). `--verify-backup
    [TIMESTAMP]` confirms a backup is restorable by checking each file's bytes
    against the `source_sha256` in its `_manifest.json`. Unlike `--update`, these
    exit codes ARE the verdict.
  - **Backup retention + off-machine mirror.** `--prune-backups [KEEP]` bounds
    `.agentteams-backups/` growth (default keep 10) and never deletes the single
    newest backup (even `KEEP 0`); `--keep-within-days DAYS` unions an age rule, and
    an indeterminate-age backup is kept (fail-safe). `--backup-mirror DIR` (or the
    `AGENTTEAMS_BACKUP_MIRROR` env var) copies each backup to a second location
    (e.g. a NAS or synced folder), best-effort and non-fatal, so the recovery net
    survives local disk loss. Tests in `tests/test_verify.py` and
    `tests/test_backup_retention.py`.

- **`--framework agents-md` — cross-tool `AGENTS.md` emitter.** A fifth target
  that emits the team as the emerging `AGENTS.md` standard (AAIF / Linux
  Foundation): a single framework-neutral repo-root `AGENTS.md` — the canonical
  file read by ~10 AI coding tools at once (Continue, Cursor, Cline, OpenAI Codex,
  Zed, Aider, Gemini CLI, …) — plus the full per-specialist team under `.agents/`
  for humans and tooling. The published `AGENTS.md` is actively neutralized of
  Copilot-specific branding/paths (no tool branding, no leaked template manifest;
  `AGENTTEAMS` fences preserved so `--update --merge` re-renders only the fenced
  regions), and routing is preserved in `references/runtime-handoffs.json`. It is
  **generate-only** — not a `--convert-from`/`--interop-from`/`--bridge-from`
  target (those paths hardcode the instructions filename); the CLI rejects those
  combinations with a clear message. Note `--framework goose` also writes a
  repo-root `AGENTS.md`; the shared ownership is documented in
  `references/bridge-refresh-safety.md`. Adapter
  `agentteams/frameworks/agents_md.py`; tests `tests/test_agents_md_framework.py`.

- **`--convert-from … --framework goose` — Goose is now a conversion target
  (Goose Phase 4).** Converting an existing team to Goose emits `.goose/recipes/*.yaml`,
  a repo-root `AGENTS.md` (team brief, front matter stripped), and the `.goosehints`
  integrator. The orchestrator's `sub_recipes` delegation wires from sources that keep
  handoffs in their agent files (`copilot-vscode`); `claude`/`copilot-cli` sources strip
  handoffs at their own generation, so they convert to valid but flat recipes (a
  source-format limitation, not a conversion defect). Implemented as five general,
  adapter-driven fixes in `convert.py` (instructions dest via `finalize_output_path`,
  render-through-adapter, sidecar emission, team-roster reconstruction, builder routing)
  — **no `goose.py` changes**. `--interop-from … --framework goose` is intentionally
  **refused**: the CAI interop representation drops the handoff graph, so the result
  would be unwired — use `--convert-from` for Goose. Tests in
  `tests/test_goose_convert_interop.py`.

- **Generated teams now verify post-merge CI/CD deployment.** Every generated
  `@git-operations` agent gains a binding invariant: when a session pushes or merges
  to a repository with GitHub Actions, it must confirm the **triggered** Actions run(s)
  — CI **and** deployment workflows (Pages/release/publish) — complete with
  `conclusion == success` before reporting the operation done, and diagnose/fix
  (`gh run view --log-failed` → fix → re-push) until green. This is distinct from the
  pre-merge required status checks that gate the merge. The procedure is documented in
  the `github-workflows-merge.reference` (*Post-Merge / Post-Push CI/CD Deployment
  Verification*), the orchestrator's **Workflow 11: Final Check** gains a matching
  closeout gate (fenced, so it reaches existing teams on `--update --merge`), and the
  git-operations Output Contract surfaces a CI/CD-status line. The obligation is
  conditional (skips cleanly when no push/merge occurred or the repo has no workflows)
  and routes cross-repo re-pushes through Rule 11 (`@repo-liaison` + `@security`). Tests
  in `tests/test_cicd_deployment_verification.py`.

### fixed

- **`module-doc-author`/`module-doc-validator` no longer false-trigger on weak
  keywords.** The two archetypes were selected whenever the project description
  contained any one of `pip / pypi / package / distribution / install / api
  reference / changelog / mkdocs / sphinx / readthedocs`. Single weak words caused
  spurious selection: an API-*consuming* reporting tool (the downstream Tracers team,
  per its `handoff-tracking-infra-remediation.md` P2b) and even agentteams' own
  `research-project` example — an academic LaTeX paper that matched solely on
  "distribution" inside *"knowledge distribution"* — were forced to carry (and
  manually deactivate) the pip-doc agents. Replaced the two loose `_ARCHETYPE_TRIGGERS`
  rows with `_should_select_module_doc()`, gated on a tight, package-exclusive decisive
  set (`pypi`, `mkdocs`, `sphinx`, `readthedocs`, `sdist`); the pair is now added from a
  single code path so a half-pair (author without validator) cannot occur. Pure-noun
  packaging descriptions naming none of those tokens are an accepted miss, recoverable
  via `selected_archetypes` / `--update`. Regenerated the `research-project` snapshot
  (removes `module-doc-author.agent.md` + `module-doc-validator.agent.md`; orchestrator
  roster + pipeline-graph re-rendered) and corrected its root `copilot-instructions.md`
  roster. Drive-by: removed pre-existing **phantom** `module-doc` entries from
  `examples/data-pipeline/expected/references/build-log.json` (listed agents the live
  pipeline already did not emit — committed at `83fe30b`, masked because the snapshot
  test excludes `build-log.json`). Corrected the stale `pip_package_name` schema prose
  that claimed the field "triggers" the archetypes (it never did — it only supplies a
  placeholder name). New `test_analyze.py` cases cover the five false-positive classes,
  the decisive-token positives, and the both-present-or-both-absent pairing invariant.
  Audited (`@adversarial` CONDITIONAL → conditions applied; `@conflict-auditor` 3
  conflicts → all resolved).

- **`technical-validator` now selected for academic/research projects.** Added
  `academic`/`thesis` to the `technical-validator` archetype trigger in
  `analyze.py`. Research deliverables make verifiable claims against authority
  sources — exactly what `technical-validator` checks — but the `research-project`
  example shipped without it, leaving the universal orchestrator/content-enricher
  handoffs to `@technical-validator` dangling (9 cross-ref warnings). Regenerated
  the `research-project` example snapshot (adds `technical-validator.agent.md`; no
  other example's selection or snapshot changes). Audited (adversarial + conflict).
- **Adversarial + conflict audit pass across scripts and docs.** Two-track audit
  (presupposition critique + drift/contradiction detection) over the package and
  documentation, with revisions: fixed a CI-red `check-durable-tmp-refs.sh`
  false-positive and its `set -e` increment-abort; changed the daily bridge
  maintenance script from `--bridge-refresh` to `--bridge-merge` (it was
  clobbering the hand-authored root `CLAUDE.md`, violating `bridge-refresh-safety.md`);
  switched `analyze.py` archetype/type keyword matching to word-boundary (kills
  `doc`→`docker` / `pip`→`pipeline` false-positive archetypes) with plural
  tolerance; guarded a notebook `source:null` `TypeError`; routed `mcp_emit` /
  `hooks_emit` / `schedule_emit` / `fence_inject` writes through `atomicio`
  (atomic + symlink-safe); added GitHub/Slack/Stripe/JWT credential patterns and
  bare-home-dir PII detection to `scan.py`; deduped eval-suite component slugs;
  fixed the man-page `store_false` synopsis and governance-agent count and
  corrected `--post-audit` to name the `copilot` CLI (not `gh`); mapped per-agent
  `tools:` to Claude `allowed-tools` so read-only governance agents are no longer
  granted `Bash`/`Write`/`Edit`; corrected the memory-index "cosine cap ~0.42"
  guidance, the `copilot-privileges` bypass contradiction, and substantial
  api-reference drift (`security-refs`, `audit`, `baseline`, `bridge-subagents`,
  `emit`, `fleet`, `handoff_payloads`, `schedule-emit`, …); and regenerated the
  example snapshots to reflect the corrected archetype selection.
- **Memory-index incremental update now matches a full rebuild.** The sed-based
  incremental path dropped each changed document's `vector_norm_sq`; it is now
  recomputed (identical to `build_memory_index`), so the on-disk artifact equals a
  full rebuild and the parity guard is meaningful.
- **Bridge merge/overwrite backs up existing target entry files** before writing
  (`.agentteams-backups` pre-write snapshot), making fleet's non-git recovery
  detail truthful.
- **Interop preserves agent name/description across the CAI round-trip** for
  `claude`/`copilot-vscode`/`goose` targets (`copilot-cli` stays body-only by
  design).
- **`atomicio._resolve_path` rejects path traversal** beyond the two-levels-up
  generation depth (defense-in-depth against an arbitrary-file overwrite).
- **`@work-summarizer` now reliably triggers at session close.** The daily
  work-summary capture was a *soft* step buried at the end of Workflow 11, reachable
  only when a session traversed a numbered workflow — so ad-hoc/direct sessions that
  executed work could close without ever invoking it. It is now a **blocking closeout
  gate** (same altitude as the `@security` / `@code-hygiene` / CI-CD gates) inside the
  fenced `available_workflows` region, so it **propagates to existing teams on
  `--update --merge`**: any session that executed work (git commits/merges, applied
  scripts, data mutations, adjacent-repo activity) cannot close until today's summary
  records it; read-only sessions skip cleanly. Final Check now also runs at the close of
  **any** executed-work session — including direct/ad-hoc requests that entered no
  numbered workflow — and runs the capture **after** the CI/CD gate so fix-commits are
  recorded. `@work-summarizer` is now explicitly **git-first**: a commit-bearing day is
  never treated as "planning-only", even when no plan artifact exists. Tests in
  `tests/test_work_summary_gate.py`.

- **`--interop-from` no longer emits reference docs (or backup copies) as bogus
  agents.** `export_to_cai` walked the source tree recursively and treated every
  `.md` it found as an agent, so `references/*.md` (and, when present, agent copies
  under `.agentteams-backups/`) were exported as ~13–14 spurious "agents" per team.
  The export now skips non-agent subdirectories (`references`, `skills`,
  `.agentteams-backups`), mirroring `convert.py`'s passthrough handling, so interop
  emits only the real agents. Test in `tests/test_interop.py`.

### changed

- **Repository filing conventions — stray plan docs no longer land at the root.**
  Root cause: concurrent direct in-repo sessions follow the "every multi-step
  request generates a plan" rule, but that rule's target (`tmp/by-week/…`) lived
  only in the generated-team instructions — so a direct session defaulted its plan
  to the cwd (repo root), and strays accumulated (plus an ignore-in-place
  `.gitignore` band-aid). Fix: (1) new **`references/filing-conventions.md`**
  states the canonical homes (`tmp/by-week/` active, `references/plans/` retained,
  `references/` published, `docs_src/` user docs, `workSummaries/` summaries);
  (2) root `CLAUDE.md` now carries the convention where in-repo sessions read it;
  (3) new guard **`tests/test_root_doc_hygiene.py`** fails on any non-allowlisted
  `*.md` at the root. Relocated 7 stray plans/reports
  (`refactor-*`, `goose-integration-plan`, `continue-dev-integration-report`,
  `CHANGES_2026-05-27`, `pypi-release-plan`) into `references/plans/`; removed the
  `pypi-release-plan.md` `.gitignore` band-aid; un-tracked two legacy files under
  `references/plans/` so the directory is uniformly local. (`security-waiver-remediation-plan.md`
  temporarily allowlisted at root pending its owning session — see filing-conventions follow-ups.)

- **Tools are documents, never agents.** Operational tools (databases, CLIs,
  build systems) previously generated full `tool-<slug>.agent.md` specialist
  *agents*. They now generate **documents**: Copilot targets emit
  `references/ref-<tool>-reference.md` (with full operational depth — config,
  invocation, verification); Claude targets emit a skill at
  `.claude/skills/tool-<tool>.md`. Library/framework tools remain lightweight
  reference docs. Tool slugs are no longer added to the orchestrator's
  `agents:` handoff roster (a tool is a resource, not a handoff target).
  - New `domain/tool-*.doc.template.md` templates carry the operational body
    without agent front matter / handoffs.
  - Manifest `output_files` gain a `"skill"` type and a per-tool `tool_slug`;
    the `tool_agents` manifest key and `detect_tool_agents()` name are retained
    for backward compatibility (they now describe tool *docs*, not agents).
  - **Migration:** legacy `tool-<slug>.agent.md` files are removed on
    `--update --overwrite` (backed up first) and flagged with a notice under
    `--update --merge`. Supersedes the earlier note that "specialist-tier tools
    never emit a `references/{slug}-reference.md`".
  - Backup/restore now preserves out-of-tree files (`../skills/*`, `../CLAUDE.md`)
    under an `__external__/` prefix so they round-trip to the correct location.

### added

- **`--fleet DIR` — first-class multi-workspace fleet update.** Runs
  `--update --merge` across every agent-infrastructure workspace under `DIR` and
  its subfolders, covering both `.github/agents/` (copilot-vscode, direct) and
  `.claude/` (bridge-**merge** for bridge consumers; direct `--framework claude`
  for native Claude teams; ambiguous `.claude` is skipped for manual review).
  Replaces the prior external batch-update script and encodes the 2026-06-04 fleet
  lessons (`references/systematic-update-lessons.md`):
  - **In-process** (re-enters `main([...])` per target) — no subprocess, so the
    exit-code/`jsonschema` ambiguity that made a successful fleet run look failed
    is gone; per-target exceptions are isolated.
  - **Git snapshot + diff safety:** before applying, each git workspace's
    agent-infra state is committed as a `chore(fleet): pre-update snapshot` (or
    HEAD when already clean) — the recoverable rollback point and diff base.
    After applying, `git diff <snapshot>` is analysed and classified by the
    **authoritative content signals** (shrink Notices, USER-EDITABLE-region
    deletions), never the exit code. Per-workspace diffs + a `report.json`/
    `summary.md` are written under `<DIR>/.agentteams-fleet/<run-id>/`.
  - **Non-destructive by construction:** merge-only; `--overwrite`, `--prune`,
    `--migrate`, `--bridge-refresh`, `--shrink-policy=allow`, and every
    single-target/mode flag are rejected in fleet mode; `.claude` is never
    bridge-refreshed. Descriptor resolution prefers `.agentteams/brief.json`
    over the thin stub (stub-trap fix). Discovery prunes `node_modules`/`.git`/
    `.worktrees`/`archive` and never recurses into `.github`/`.claude` internals.
  - **Default is a dry-run preview**; pass `--yes` to apply. Flags:
    `--fleet-frameworks {github,claude,both}`, `--fleet-report DIR`.
  - Module `agentteams/fleet.py`; 15 tests in `tests/test_fleet.py`. Plan +
    adversarial/conflict audit under
    `references/plans/fleet-update-integration-2026-06-08.plan.md`.

### fixed

- **A missing `jsonschema` no longer crashes a completed `--update`.** The
  post-merge attestation writers (`_write_delivery_receipt`, `_write_eval_suite`,
  `_write_model_routing`, `_validate_memory_index_schema`, `_write_memory_index`)
  did a hard `import jsonschema` *after* the merge had already written every
  agent file. If the running interpreter lacked `jsonschema`, the resulting
  `ModuleNotFoundError` escaped each writer's non-fatal handler in `main()` and
  aborted the whole run with a traceback (exit 1) — turning a fully successful,
  non-destructive merge into an apparent hard failure. This bit a 38-repo fleet
  update where the batch ran under an interpreter without the dep. A new
  `_require_jsonschema(error_cls, artifact)` helper now degrades a missing module
  to the writer's own non-fatal error, so the merge completes (exit 0) and prints
  `!  … write failed (build-log healed)`; the artifact is re-emitted on the next
  `--update`. Regression tests in `tests/test_delivery_receipt.py`. See
  `references/systematic-update-lessons.md`.

### docs

- **New `references/systematic-update-lessons.md`** — fleet (multi-repo) update
  lessons: exit-code ≠ merge outcome, how to read bulk diffs (fenceless generated
  files + intel churn dominate; the real signals are `USER-EDITABLE` deletions and
  shrink-guard notices), `--merge` reverting consumer-side workarounds, and the
  output-dir-relative backup path. Cross-linked from the update-lifecycle guide
  and the fleet-update governance docs.
- **Corrected fleet-update governance docs.** `fleet-update-authorization-policy.md`
  and `fleet-update-scope-boundary.md`: exit-code-based HALT replaced with a
  content audit (a non-zero exit is frequently a post-merge attestation crash over
  a successful merge); backup path corrected to `<output_dir>/.agentteams-backups/`
  (output-dir-relative, not a top-level `.backups/`); discovery command excludes
  `.worktrees/`/`archive/` copies; Condition D and review dates reconciled. Fixed
  `section-fencing-guide.md` to state the real default `--shrink-policy=preserve`
  (was incorrectly "warn"). The `agent-updater` template's ERROR rung was rewritten
  so a non-zero exit is no longer equated with a partial write / restore trigger.

### changed

- **`--update --merge` now auto-retrofits fence markers onto legacy files by
  default.** Previously, files without `AGENTTEAMS` fence markers (legacy/
  pre-fence agent docs) were SKIPped on update, so their template regions never
  updated. Now, when run with `--yes`, `--update --merge` auto-wraps each
  eligible legacy file's body in a `content` fence (backing the original up
  first) so its template region merges — bringing long-stale downstream docs
  current. It is **content-safe**: the pre-injection backup retains the original
  body and the shrink-guard still suppresses material template shrinks (so richer
  legacy content is preserved, recoverable, never silently lost). Opt out with
  **`--no-add-fence-markers`** to keep the conservative skip-legacy behaviour.
  `--yes`-gated (no file mutation without it). Distinct from the standalone
  per-file `--add-fence-markers PATH` retrofit. Plan + adversarial audit under
  `references/plans/`.
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
  `is_enabled`. See the API reference at `docs_src/api-reference/host-features.md`.
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
