# Integration Plan: Goose (Block / AAIF) into agentteams

**Date:** 2026-06-15
**Scope:** Add `goose` as a first-class target "along the same lines as claude and copilot" — i.e. a **framework adapter** (primary build path) **and** a **bridge target** (since for claude the bridge is the canonical consumption path, true parity requires both).
**Status:** PLAN (no code written yet). All codebase touch points are file:line-verified; all Goose formats are primary-source-verified (see §0).

---

## 0. Gating verdict (the prerequisites the user asked about)

**Q1 — Actively maintained? YES, definitively.** v1.37.0 released 2026-06-03; commits landing 2026-06-15; weekly release cadence; ~49.5k stars; 189 open / ~6,970 closed PRs; a v2.0 RC line in progress. ([releases](https://github.com/aaif-goose/goose/releases))

**Q2 — Linux Foundation? CONFIRMED — governance, not just "usage."** Block contributed Goose to the **Agentic AI Foundation (AAIF), a directed fund under the Linux Foundation**, announced **2025-12-09**, anchored alongside Anthropic's **MCP** and OpenAI's **AGENTS.md**. ([LF press release](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)) Precise framing: the project is **governed under the LF via AAIF** (a stronger durability signal than "LF uses it as a tool"); Block also runs it internally at scale.

> **⚠️ Repo/docs moved (2026-04-07):** `block/goose` → **`github.com/aaif-goose/goose`**; docs → **`goose-docs.ai`**. All builder-template links, bridge references, and docs must point at the new locations. Some old doc slugs 404 (`config-file` → `config-files`).

**Conclusion: both prerequisites met → proceed with the plan below.**

---

## 1. Why Goose is a structurally GOOD fit (unlike continue.dev)

agentteams' core product is an **orchestrator that delegates to specialized agents**. continue.dev had *no documented delegation primitive* (the integration would have degraded to N independent assistants). **Goose natively supports the orchestrator→specialists topology** via two mechanisms:

- **`sub_recipes`** — a recipe statically declares specialist recipe files; Goose generates a delegation tool per sub-recipe and runs each in an isolated session. This is the **direct analog of agentteams handoffs** and the backbone of the integration.
- **subagents** (`delegate`/`load` tools from the `summon` platform extension) — dynamic, runtime delegation.

This means the Goose adapter can set **`handoff_delivery_mode() = "native"`** (handoffs → `sub_recipes`), putting Goose on par with `copilot-vscode` rather than the degraded `claude`/continue posture.

**Hard constraint to design around:** sub-recipes are **one orchestration layer deep — no nesting** (a sub-recipe cannot declare its own sub-recipes); parallelism is experimental and capped at **10 concurrent workers**; subagents cap at 25 turns / 5-min timeout. See §5.

---

## 2. Concept mapping: agentteams → Goose

| agentteams concept | Goose target | Fidelity |
|---|---|---|
| Specialized agent (e.g. `quality-auditor`) | A **recipe** YAML at `.goose/recipes/<slug>.yaml` (`title`, `description`, `instructions`, `extensions`) | **Good** |
| Orchestrator agent | A top-level **recipe** with a `sub_recipes:` list (one entry per specialist) + the `summon` platform extension | **Good** |
| Handoff (orchestrator → specialist) | A `sub_recipes` entry (`name`, `path`, optional `values`/`description`) → `handoff_delivery_mode = "native"` | **Good** (1 layer) |
| Team instructions (`CLAUDE.md` / `copilot-instructions.md`) | **`AGENTS.md`** at repo root (Goose reads it first by default), with `.goosehints` as the Goose-native alternative | **Good** |
| Agent tools / `allowed-tools` | Recipe `extensions:` list (`builtin` `developer` for file ops; `stdio`/`streamable_http` for MCP) | **Good** |
| Per-agent model selection | Recipe `settings: { goose_provider, goose_model, temperature, max_turns }` | **Good** (config.yaml is global-only; recipe `settings` is the per-agent lever) |
| Skills (Claude-only concept) | No first-class analog → fold into recipe `instructions` (or `.goosehints`) | Degraded (acceptable) |
| Cross-cutting agents (security, git-operations, cleanup) a specialist hands off to (depth-2+) | **Reference, don't delegate**: the specialist `summon.load("<slug>")`s the cross-cutting recipe into its own context (runtime), and/or `@file`-includes its guidance (static). See §10 | **Good** (faithful, no nesting violation) |

---

## 3. The `GooseAdapter` (FrameworkAdapter subclass)

New file: `agentteams/frameworks/goose.py`. Implements the verified 6-method contract from `frameworks/base.py`.

| Method | Implementation |
|---|---|
| `framework_id` | returns `"goose"` |
| `get_agents_dir(project_path)` | `project_path / ".goose" / "recipes"` |
| `get_file_extension(file_type)` | `agent` → `".yaml"`; `instructions` → `".md"` (emit `AGENTS.md`); `builder` → `".md"` |
| `render_agent_file(content, agent_slug, manifest)` | **The core transform** — markdown-agent → **recipe YAML** (details below) |
| `render_instructions_file(content, manifest)` | strip YAML front-matter → emit plain-prose `AGENTS.md` (team overview + routing notes) |
| `supports_handoffs()` | **`True`** |
| Optional `handoff_delivery_mode()` | **`"native"`** (handoffs become `sub_recipes`) |
| Optional `finalize_output_path(rel_path, file_type)` | map the instructions file to repo-root `AGENTS.md` (mirrors how `claude.py` maps to `CLAUDE.md`) |
| Optional `render_skill_file` | no-op default (Goose has no skills) |

**`render_agent_file` — the one genuinely new piece of work.** Unlike `claude.py` (which only swaps front-matter on a markdown body), Goose's agent file is a *structured YAML document*. The adapter must:
1. Parse `name`/`description`/tools from the rendered agent's existing front-matter (reuse `_strip_yaml_front_matter` + a small front-matter reader).
2. Use the inherited **`extract_handoffs(content)`** (already returns `[{label, agent, prompt, send}]`) to build the `sub_recipes` list **for the orchestrator only**.
3. Emit a recipe dict → YAML (block scalar `instructions: |` holds the markdown body verbatim).

Build a tiny `_recipe_to_yaml(dict)` helper (or depend on `pyyaml`, already used elsewhere) rather than hand-rolling YAML.

### 3.1 Example generated files

**Specialist** — `.goose/recipes/quality-auditor.yaml`:
```yaml
version: "1.0.0"
title: "Quality Auditor"
description: "Audits generated artifacts for correctness and standards compliance."
instructions: |
  You are the Quality Auditor for <project>.
  <full agent body prose, verbatim from the rendered template>
extensions:
  - type: builtin
    name: developer
    bundled: true
    timeout: 300
settings:
  goose_model: "claude-sonnet-4-6"
```

**Orchestrator** — `.goose/recipes/orchestrator.yaml` (handoffs → `sub_recipes`):
```yaml
version: "1.0.0"
title: "Orchestrator"
description: "Routes work to specialist agents and integrates their output."
instructions: |
  You coordinate a team of specialists. For each task, delegate to the matching
  sub-recipe and integrate results. <orchestrator body prose>
extensions:
  - type: platform        # 'summon' is required when a recipe declares sub_recipes
    name: summon
sub_recipes:
  - name: "quality_auditor"
    path: "./quality-auditor.yaml"
    description: "Audit artifacts for correctness/standards"
  - name: "primary_producer"
    path: "./primary-producer.yaml"
```

**Instructions** — repo-root `AGENTS.md` (team overview + how to run): plain prose, no front-matter; Goose loads it by default ahead of `.goosehints`.

> **Handoff-prompt nuance:** agentteams handoffs carry a free-text `prompt`; Goose `sub_recipes` pass data via `values`/context, not a free-text prompt. Decision (§7): either (a) drop the prompt into the sub-recipe `description` (lossy but simple), or (b) generate a `task` parameter on each specialist recipe and pass the prompt via `values`. Recommend (a) for v1.

---

## 4. Touch-point checklist — adapter path (file:line verified)

- [ ] **New** `agentteams/frameworks/goose.py` (`GooseAdapter`).
- [ ] Register in **all 3** registries: `build_team.py` `FRAMEWORKS` (~L82-85), `agentteams/interop.py` `_ADAPTERS` (~L22-25), `agentteams/convert.py` `_ADAPTERS` (~L43-46). *(CLI `--framework` choices derive from `FRAMEWORKS.keys()` at `build_team.py` L161/457/481 — auto-updates.)*
- [ ] Builder template **new** `agentteams/templates/builder/team-builder-goose.template.md` + selector entry in `analyze.py` builder dict (~L1331-1334). Point links at `goose-docs.ai`.
- [ ] Instructions path logic: `analyze.py` (~L1319-1321) — add the `goose → AGENTS.md` branch; check sibling conditionals at `render.py` L328, `interop.py` L175/244, `drift.py` L317, `emit.py` L1365.
- [ ] Skill conditionals (`analyze.py` L1088, L1184-1187, L1485): Goose emits **no** skills — ensure it falls into the non-skill path.
- [ ] Schema enum: `schemas/team-manifest.schema.json:39` add `"goose"`.
- [ ] Tests: `tests/test_frameworks.py` — add a `GooseAdapter` test class (framework_id, recipe YAML shape, sub_recipes from handoffs, AGENTS.md instructions, extension list).
- [ ] Docs: `README.md` capability table + default-dir list, usage docstring, `CHANGELOG.md`, a new `docs_src/api-reference/` page.

---

## 5. Touch-point checklist — bridge target (to truly match claude)

A `claude → goose` (or `copilot-vscode → goose`) bridge writes fenced pointer files into a Goose project so it reuses canonical agents without regeneration.

- [ ] `bridge.py` validation set (**L99-102**) — add `"goose"` to the source/target allow-sets.
- [ ] `bridge.py` `_render_target_files()` (**L527-616**) — add a `goose` branch writing entry files with `AGENTTEAMS-BRIDGE` fences: a repo-root `AGENTS.md` (or `.goosehints`) pointing at `references/bridges/<src>-to-goose/agent-inventory.md` + quickstart.
- [ ] `interop.detect_framework()` (**L47-75**) — detect Goose by `.goose/recipes/` and/or `.goosehints`.
- [ ] `host_features.py` (L18-38) — add `goose` namespace + any bridge feature flags.
- [ ] `tests/test_bridge.py` — parametrize `(*, "goose")` source/target pairs + a `_goose_recipe(slug)` builder.

### 5.1 Bridge SAFETY precondition (do NOT skip)
The `--bridge-refresh` Pre-Flight checks in `references/bridge-refresh-safety.md` are written **only for claude entry files**. A Goose bridge introduces **new destructive-overwrite surfaces**:
- **`AGENTS.md`** is a *shared, multi-tool standard file* — Cursor, Codex, Cline, etc. also read it. Overwriting it can clobber content other tools depend on. **Treat as merge-only / default-unfenced; never `--bridge-refresh` blindly.**
- **`.goosehints`** / any `config.yaml` are user-committed ("treat like code").

**Precondition:** extend `bridge-refresh-safety.md` Pre-Flight checks 1-4 to enumerate `AGENTS.md`, `.goosehints`, `.goose/` before any Goose `--bridge-refresh` ships. (`--bridge-merge` remains the safe default.)

---

## 6. Constraints, gaps & risks (be explicit)

1. **One-layer delegation only — SOLVED via references (§10).** Orchestrator→specialists (1 layer) fits as `sub_recipes`; **specialist→cross-cutting handoffs (depth-2+) are reinterpreted as `summon.load` / `@file` references**, not delegation. This is faithful (the relationship and the cross-cutting agent's guidance are preserved) and respects Goose's hard no-nesting rule. Full model in §10.
2. **`sub_recipes` is experimental** (subagents are not); parallel execution capped at 10 workers; subagents cap at 25 turns / 5-min timeout. Acceptable for v1; note in docs.
3. **No skills concept** — Claude-only skills fold into recipe instructions (lossy but fine).
4. **config.yaml is global-only** — per-agent model/provider must go in recipe `settings`, not a repo config. The adapter should **not** try to write a global `~/.config/goose/config.yaml`.
5. **Format gotchas the emitter must respect:** emit `.yaml` (CLI rejects `.yml`); `extensions` is a **list** in recipes (a map in config.yaml); recipe extensions use `env_keys` (not `envs`); **do not emit a `context:` field** (not a recipe field); use `streamable_http`, **not** the deprecated `sse` transport; `summon` platform extension is required whenever `sub_recipes` is present.
6. **AGENTS.md collision** (bridge mode) — see §5.1.
7. **Moving target** — Goose ships weekly and a v2.0 RC is in flight; the recipe schema could shift. Mitigation: pin `version: "1.0.0"` in emitted recipes, add a recipe-shape test, and validate generated recipes with `goose recipe validate` in CI if Goose is installable there.

---

## 7. Decisions — RESOLVED (2026-06-15)
1. **Instructions target → `AGENTS.md` is canonical**, and **`.goosehints` is the Goose-native integrator** that pulls AGENTS.md into the system prompt via `@AGENTS.md` (see §10.1). *(Source correction: Goose's compiled `CONTEXT_FILE_NAMES` default is `[".goosehints", "AGENTS.md"]` — `.goosehints` is read first — so a `.goosehints` that `@`-includes `AGENTS.md` is the robust integration point even if a project customizes the discovery list.)*
2. **Handoff `prompt` mapping → sub-recipe `description`** for v1 (simple, lossy-but-adequate).
3. **Nested / cross-cutting agents → reinterpret via tools/references, NOT delegation.** Confirmed possible and source-verified. Depth-1 = `sub_recipes` (delegation); **depth-2+ = `summon.load` (runtime reference) and/or `@file` includes (static reference)** — full model in **§10**. This replaces the earlier "flatten everything to hub-and-spoke" idea with something more faithful to the agentteams DAG.
4. **Bridge scope → adapter first (Phase 1), bridge later (Phase 2).** Per decision-3: build the adapter, test locally on an uncommitted branch (`goose-integration`), then develop the goose-native-agents plan (**§11**) and the bridge.

---

## 8. Phasing & effort

| Phase | Work | Rough effort |
|---|---|---|
| **0 — Decisions** | Resolve §7 (esp. instructions target + cross-cutting flattening) | hours |
| **1 — Adapter** | `GooseAdapter` (recipe-YAML emitter is the only novel logic), 3× registration, schema enum, builder template, `analyze.py` wiring, `test_frameworks.py` | **~1.5–2.5 days** (more than `claude.py` because of the markdown→structured-YAML transform; less, if `extract_handoffs` is reused for `sub_recipes`) |
| **2 — Bridge** | `bridge.py` validation + render branch, `detect_framework`, `host_features`, `test_bridge.py`, **+ extend `bridge-refresh-safety.md`** | **~1–2 days** |
| **3 — Docs** | README capability table/dirs, CHANGELOG, api-reference page; verify `goose recipe validate` on generated output | **~0.5 day** |

**Validation gate:** generate a sample team with `--framework goose`, run `goose recipe validate .goose/recipes/orchestrator.yaml`, and confirm `goose run --recipe .goose/recipes/orchestrator.yaml --no-session` delegates to a specialist.

---

## 10. Standardized nested-structure reinterpretation (delegate vs reference)

**Problem:** agentteams produces a delegation DAG — orchestrator → workstream specialists → cross-cutting agents (security, git-operations, cleanup, navigator, …). Goose `sub_recipes`/subagents are capped at **one delegation layer** (source-verified: `if session.session_type == SessionType::SubAgent { return Err("Delegated tasks cannot spawn further delegations") }`). So depth-2+ handoffs cannot be delegation.

**Answer to "can we nest via tools/references instead of agents?" — YES**, and the mechanisms are documented + source-verified:
- **`summon.load(source)`** loads another recipe/agent/skill's full content **into the current session** — *no child spawned, no depth cap*; the cap is enforced only on `delegate`. `load` is callable from inside a delegated child, so a depth-1 specialist can `load` a depth-2 agent. This is the runtime "reference instead of agent" primitive.
- **`@file` content-injection** in `.goosehints`/`AGENTS.md` — recursive to depth 3, git-root-bounded, 128 KB/file — the static layered backbone. (Note: `@file` does **not** work inside recipe `instructions`/`prompt`; for in-recipe file injection use a **File-type parameter** or `{% extends %}` recipe template inheritance.)

### 10.1 The two-axis classification (deterministic, computed from the manifest)
Every agentteams agent is classified on two axes; the pair decides its Goose representation.

**Axis A — role:**
- **Active worker** — produces/modifies artifacts (e.g. primary-producer, quality-auditor) → gets a **recipe**.
- **Advisory/policy** — supplies rules/guidance, doesn't itself produce deliverables (e.g. style-guardian rules, a security checklist) → may be a **reference doc** (`references/<slug>.md`) instead of a recipe.

**Axis B — delegation depth from the orchestrator** (min hops in the handoff DAG):
- **Depth 0 — orchestrator** → `.goose/recipes/orchestrator.yaml`, declares `summon` + `sub_recipes` of its depth-1 targets.
- **Depth 1 — orchestrator's direct delegates** → `.goose/recipes/<slug>.yaml`, wired as orchestrator `sub_recipes` (TRUE delegation, isolated session).
- **Depth ≥2 — reached only via a specialist** → **NOT a sub_recipe.** Reinterpret the edge:
  - *active worker* → emit its recipe AND inject into the parent specialist's `instructions` a directive: *"When you need `<X>`, call `load("<x-slug>")` to bring its guidance into your context"*, and declare the `summon` extension on that specialist. (Genuine isolated re-delegation, if ever required, must go **hub-and-spoke** through the orchestrator, because a `SubAgent` session cannot `delegate`.)
  - *advisory/policy* → emit `references/<slug>.md` and `@file`-include it where needed (in `AGENTS.md` for always-on policy, or `load`/File-param for on-demand).

### 10.2 Translation algorithm (what the adapter does)
1. Build the handoff graph from the manifest; compute each agent's **min depth** from the orchestrator.
2. Emit a recipe per **active-worker** agent; emit a `references/<slug>.md` per **advisory** agent.
3. Orchestrator `sub_recipes` = its **depth-1** handoff targets only.
4. For every handoff edge at **depth ≥2** (`specialist → X`): rewrite as a `load("X")` directive (active) or `@file` include (advisory) inside the parent's instructions; ensure `summon` is declared on the parent. **No edge is dropped — every handoff is preserved, just as a reference rather than a nested delegation.**
5. An agent reachable at BOTH depth-1 (direct from orchestrator) and depth-2 (via a specialist) is emitted **once** and wired both ways (orchestrator `sub_recipe` + specialist `load`).

### 10.3 Faithfulness statement
- Orchestrator→specialist delegation: preserved exactly (isolated `sub_recipes` sessions).
- Specialist→cross-cutting: preserved as an explicit in-context reference. For *advisory* cross-cutting agents (most of them — security policy, conventions), `load`/`@file` is arguably **more** faithful than spawning a separate agent, since their job is to inject rules the specialist must follow. The only true loss is isolated-session execution for a depth-2 *active* worker, which is rare and handled hub-and-spoke.

### 10.4 `.goosehints` ⇄ `AGENTS.md` integration (decision 1)
- **`AGENTS.md`** (repo root) = canonical team content: roster, routing, run instructions. Loaded by Goose's default discovery.
- **`.goosehints`** (repo root) = thin Goose integrator: a leading `@AGENTS.md` include (guarantees AGENTS.md reaches the prompt even under a customized `CONTEXT_FILE_NAMES`) + a short "Goose operational notes" block (entrypoint recipe, how `sub_recipes`/`load` delegation works here). *Tradeoff flagged: in the default config both files load, so `@AGENTS.md` double-injects the same content — benign (identical text), and the include buys robustness. A `--goose-hints-plain` flag can switch `.goosehints` to a plain reference if double-load is undesirable.*

---

## 11. Phase 4 (forward plan): goose-native agents
Phase 1 produces a faithful **translation** of the existing copilot/claude-shaped templates into Goose recipes. A follow-on effort should author agents **designed natively for Goose**, exploiting capabilities the translation can't use from the legacy templates:
- **Recipe `parameters`** (typed inputs, `select`/`file` types) for parameterized specialists instead of prose-only agents.
- **`settings`** per recipe (provider/model/temperature/max_turns) tuned per specialist.
- **`retry.checks`** (shell/structured validation) for self-verifying agents.
- **`response.json_schema`** for structured-output specialists (e.g. an auditor that returns findings JSON).
- **`goose schedule`** for cron/recurring team workflows.
- **Native subagent parallelism** (≤10 workers) for fan-out specialists.
- **Goose-native builder template** authored against `goose-docs.ai` conventions rather than ported from the copilot intake interview.
This phase is a separate plan (and likely a separate set of `templates/builder/` + domain templates), to be written after the adapter is validated locally.

---

## 12. Bottom line
Goose clears both gating bars (actively maintained; LF-governed via AAIF) and is a **better structural fit than continue.dev** because it natively models orchestrator→specialist delegation (`sub_recipes`) AND offers a **source-verified, faithful way to represent deeper nesting as references/tools** (`summon.load` + `@file`) rather than dropping it. So `GooseAdapter` is `native`-handoff at depth-1 and reference-based at depth-2+, with no loss of edges. The novel implementation work is (a) a markdown→recipe-YAML transform and (b) the depth-classification pass in §10.2; everything else reuses the verified adapter contract and the same touch points claude/copilot use. **Sequence:** Phase 1 adapter (this branch, tested locally before any commit) → Phase 2 bridge (with the §5.1 safety-doc extension as a hard precondition) → Phase 4 goose-native agents (§11).
