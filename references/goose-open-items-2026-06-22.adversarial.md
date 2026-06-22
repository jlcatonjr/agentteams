# Adversarial Panel Audit — Five Goose Forward-Implementation Plans (2026-06-22)

Plans P1–P5 under `references/plans/goose-*-2026-06-22.plan.md`. goose 1.37.0 installed; all
claims probed against source + live commands.

## P1 — Live `goose run` delegation validation
- Generated goose `orchestrator.yaml` DOES carry `sub_recipes` (`goose.py:201-232`) — direct-build
  orchestrator is the right vehicle; bridge-orchestrator (no sub_recipes, `:510`) is not. **Accepted.**
- `goose run --recipe … --no-session --max-turns N` is the right non-interactive form (`goose run --help`);
  the W6 `prompt:` rides in the recipe. **Accepted.**
- "exits 0 on error" is HALF true: **missing key → exit 1** (config-resolution); **bogus key → exit 0**
  (provider 401). Plan's blanket wording wrong for the missing-key class. **Accepted w/ mitigation.**
- "executable now": model slug `qwen/qwen3.6-35b-a3b` IS in the live catalog, but `OPENROUTER_API_KEY`
  is unset here and `~/.config/agentteams/goose-sources.json` is absent → run aborts exit 1 before any
  LLM call. **Rejected (not reproducible in this env); slug is fine.**
- **Verdict: PASS with conditions** — skip-by-default `skipif(no OPENROUTER_API_KEY)` is MANDATORY;
  doc the missing-key class as exit-1, distinct from the exit-0 provider-error class.

## P2 — Goose-as-SOURCE bridging (hardest)
- D3 confirmed empirically: today's md-only `_collect_source_files` (`bridge.py:493`) returns `[]` for a
  `.yaml`-only goose source. Fix via `adapter.get_file_extension("agent")` (`.yaml` for goose,
  `goose.py:280-283`) is correct. **Accepted.**
- `_extract_inventory` (`bridge.py:465`) and the source allow-set (`bridge.py:100`) genuinely need goose
  branches. **Accepted.**
- `detect_framework` (`interop.py:43-71`): a `.yaml` goose dir falls through to `return "copilot-cli"`
  today (misdetection); gate the new branch on the `.goose/recipes` path, place after existing checks,
  add per-framework tests. **Accepted w/ mitigation.**
- **Touch points MISSED:** `export_to_cai`/`import_from_cai` (`interop.py:100,160`) and `convert.py`
  classification (`:213,238`) are `.md`-only — needed if goose-as-source flows through convert;
  inventory `source_file` display/sort (`bridge.py:482`) and manifest rel paths (`_compute_hash_rows`,
  parent `.goose` → `recipes/foo.yaml`) untested. **Rejected (D-list incomplete).**
- **Verdict: PASS with conditions** — D3 as extension-driven selection AND assert BOTH directions
  (goose `.yaml` hashed; copilot/claude still excludes `_build-description.json`) in one test module;
  add the missed interop/convert touch points if goose-as-source must convert, not only bridge.

## P3 — Subagent stubs for the copilot-vscode → goose bridge
- `_emit_recipe` (`goose.py:613-686`) produces valid recipe YAML passing `_validate_recipe_yaml`. **Accepted.**
- Claude `emit_subagent_stubs` is a BRIDGE-path mechanism (`bridge.py:364-368`, gated by host-feature
  `bridge:copilot-vscode-to-claude:subagents`, `host_features.py:42-43`) — mirror is sound. **Accepted.**
- "Opt-in default byte-identical" achievable, BUT the plan omits registering the new token in
  `host_features._KNOWN_FEATURES` for the goose-bridge namespaces (today `{"mcp"}` only, `:50-52`);
  unregistered → `HostFeatureError` (`:56`). **Accepted w/ mitigation.**
- Collision: goose bridge writes only `bridge-orchestrator.yaml`; reserved-skip MUST include
  `bridge-orchestrator` (and defensively `orchestrator`/`team-builder`), write-only-when-missing. **Accepted w/ mitigation.**
- **Verdict: PASS with conditions** — register the new token in `host_features._KNOWN_FEATURES` for the
  three goose-bridge namespaces; hard-skip `bridge-orchestrator`.

## P4 — Add Goose to the daily bridge-maintenance loop
- Preflight `compgen -G "$SOURCE_DIR/*.agent.md"` checks the SOURCE (copilot-vscode), unaffected by a
  goose TARGET; loop body target-agnostic; `run_bridge` accepts goose target. **Accepted.**
- Goose `--bridge-check` in OUTPUT_ROOT=repo-root context: ran live → **PASS, rc=0.** **Accepted.**
- Daily `--bridge-merge` touches the SHARED `AGENTS.md` fenced-only (`_merge_target_file`, `:852-889`);
  repo already has a fenced `AGENTS.md` → no first-create. Manifest `generated_at` churns each run. **Accepted w/ mitigation.**
- **Verdict: PASS with conditions** — extend the script comment block to document the goose target's
  SHARED `AGENTS.md`/`.goosehints` fenced-merge (currently CLAUDE.md-only).

## P5 — API-reference page for `agentteams/goose_config.py`
- Parity coverage is name-match only: page stem (dash→underscore) → `agentteams/<name>.py`
  (`check_api_doc_parity.py:74-81`). Any `.md` at the path clears it; no content/heading requirement.
  "Stricter rule" worry unfounded. **Accepted.**
- Page MUST be `goose-config.md` (dash) to normalize to `goose_config`. **Accepted.**
- Adding the page removes exactly that COVERAGE_GAP entry (live-confirmed). **Accepted.**
- **Verdict: PASS** — name the file `goose-config.md`; don't gate on `--strict`/content.

## Cross-plan risks
1. **P2 ↔ task-2 hashing (HIGH):** md-only `_collect_source_files` hashes ZERO for a `.yaml` goose source
   (verified). Any PR touching that function must run BOTH the goose-yaml and copilot-md-excludes-junk
   assertions in one test module.
2. **Ordering P2 → P3/P4:** P3/P4 are TARGET-side (copilot→goose), independent of P2's SOURCE-side change
   and can land first. Land P2 and P4 with a shared regression gate (both read `_collect_source_files`),
   or land P4 before P2.
3. **P3 host-features registry (latent break):** unregistered `…-to-goose:subagents` token raises
   `HostFeatureError` at parse time — register it.
4. **P1 environment reality:** P1 alone needs live external state (key + sources file) absent here; the
   other four are fully exercisable offline. Downgrade "executable now" → "executable where the key is present."
