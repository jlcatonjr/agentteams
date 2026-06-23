# Phase-4 "Goose-Native Agents" — Design & Roadmap

Status: **DESIGN / ROADMAP — not yet implemented.** Scopes the long-horizon
"goose-native agents beyond source-bridging" idea flagged in the 2026-06-22 Goose
handoff (§8) as a forward idea, not an open task. This document defines what
"goose-native" means, does a grounded gap analysis against the shipped adapter,
proposes a phased low-risk increment path, and records adversarial/conflict
considerations. No behavior changes ship with this doc.

---

## 1. What "goose-native" means (definition)

Today a Goose team is produced two ways: (a) `--framework goose` renders the
universal/domain agent templates into Goose recipes via the recipe **adapter**,
and (b) a **bridge** points a Goose runtime at a copilot/claude source team. Both
treat the recipe as a markdown-delivery vehicle: the agent's prose becomes the
recipe `instructions`, with delegation via `sub_recipes`/`summon`.

**"Goose-native"** = generating teams that exploit Goose recipe capabilities the
adapter does not yet use — typed `parameters`, structured `response` schemas,
self-validating `retry`, and per-agent `extensions` — so a generated Goose team is
first-class in Goose terms, not just transliterated copilot markdown.

## 2. Current state (grounded)

`agentteams/frameworks/goose.py::_emit_recipe` emits exactly:
`version`, `title`, `description`, optional `prompt`, `instructions` (literal
block), optional MCP `# notes`, `extensions` (developer/summon/MCP), and optional
`sub_recipes`. It does **not** emit `parameters`, `response`, `retry`, or recipe
`settings`.

`agentteams/frameworks/goose.py::_validate_recipe_yaml` (regex-only; the codebase
deliberately avoids a YAML dependency) asserts `version:"1.0.0"`, non-empty
`title`, an `instructions: |` block; **forbids** `model:`, `envs:`, `type: sse`,
`context:`; and resolves `sub_recipes` paths on disk. `cli/recipe_check.py`
delegates to it. **Contract: any newly-emitted recipe field must be added to these
guards (and must not collide with a forbidden-key pattern), or the new recipes
ship unvalidated.**

## 3. Gap analysis (Goose recipe features vs. what we emit)

The Goose recipe schema ([recipe reference][rr]) supports these fields we don't use:

| Recipe field | Goose semantics | Native use for a generated team |
|---|---|---|
| `parameters` | typed inputs (`key`, `input_type` string/number/select/file, `requirement`, `default`, `description`) | declare a team/workstream's inputs (brief path, target dir, component id) instead of hard-coding in `instructions` |
| `response` | output `json_schema` (typed result) | auditor/validator agents (conflict-auditor, technical-validator, quality-auditor) emit **structured findings** a parent recipe can branch on |
| `retry` | `max_retries`, `timeout_seconds`, `checks` (type+command), `on_failure` | self-validating agents re-run until a check passes (e.g. tests green, `recipe-check` clean) |
| per-agent `extensions` | richer MCP/extension wiring per recipe | scope each agent's tools/MCP to its role (least privilege, mirroring the claude `allowed-tools` mapping) |

## 4. Phased increment path (each: small, safe, test-backed, golden-regen)

Ordered by value ÷ risk. Each phase extends `_emit_recipe` to emit the field,
extends `_validate_recipe_yaml` + `recipe_check` to validate it (and confirms it
trips no `_RECIPE_FORBIDDEN_*`), adds unit tests, and **regenerates the example
goldens against the updated templates** (per the handoff's golden-regen rule).

- **4a — `parameters` (recommended first slice).** Lowest risk: additive, optional,
  no behavior change when a brief declares none. Emit declared workstream/brief
  inputs as recipe `parameters`. Source from `manifest` (component slugs, brief
  fields). Guard: add a `parameters:`-shape check; ensure no forbidden-key overlap.
- **4b — `response` json_schema for typed auditors.** Emit a `response.json_schema`
  for the read-only auditor archetypes so findings are machine-consumable. Guard:
  validate the json_schema block shape.
- **4c — `retry` with success-criteria.** For validation agents, emit `retry` with
  a `checks` command (e.g. the project's test/recipe-check command). Guard: validate
  `max_retries`/`checks` shape; cap `max_retries` to avoid runaway loops.
- **4d — per-agent extension scoping + fleet/recipe-check parity.** Map each agent's
  declared `tools:` to a minimal `extensions` set (parity with the claude
  `allowed-tools` least-privilege mapping); extend `recipe-check`/fleet to cover the
  new fields.

## 5. Recommended first slice

Implement **4a (`parameters`)** as a standalone PR: it is purely additive (a team
with no declared parameters emits byte-identical recipes — preserving STABILITY for
existing consumers), demonstrably valuable (typed inputs for `goose run --recipe`
in CI), and the smallest unit that exercises the full emit→validate→test→golden-regen
loop the later phases reuse. Defer 4b–4d to follow-on PRs once 4a's pattern lands.

## 6. Adversarial + conflict considerations (self-audit)

- **Presupposition — the recipe schema supports these fields:** verified against the
  Goose recipe reference; all four (`parameters`/`response`/`retry`/`extensions`) are
  real. ([rr], [subrecipes])
- **Presupposition — additive emission is non-breaking:** TRUE only if each field is
  emitted **conditionally** (absent when the brief declares none) so existing
  goldens/consumers are byte-identical — a hard requirement for every phase
  (STABILITY.md; the goose adapter API is explicitly *not yet* under the stability
  policy, which gives design latitude but the no-silent-break rule still applies to
  shipped consumers).
- **Conflict — validation guards:** `_validate_recipe_yaml` is regex-only and has
  forbidden-key checks (`model:`/`envs:`/`type: sse`/`context:`). Each new field MUST
  (a) be added as a positive check and (b) be confirmed not to match a forbidden
  pattern (e.g. a `parameters` default that contains the substring `context:` must
  not false-trigger). Each phase needs a regression test asserting the new field
  validates and the forbidden checks still fire.
- **Conflict — bridge vs. native:** the bridge path (`bridge_subagents_goose.py`)
  emits pointer/stub recipes; native emission must not change bridge output (the
  bridge stays a separate, opt-in code path).
- **Risk — `retry` runaway loops (4c):** cap `max_retries` and require a bounded
  `timeout_seconds`; never emit an unbounded retry.

## 7. Out of scope (explicitly)

Goose-as-orchestrator-of-non-goose-runtimes, a goose-native eval harness beyond the
existing `eval_suite`, and any change to the shipped bridge contracts. Those are
separate, larger initiatives.

## Sources

- Goose recipe reference: [block.github.io/goose/docs/guides/recipes/recipe-reference][rr]
- Goose sub-recipes: [block.github.io/goose/docs/guides/recipes/sub-recipes][subrecipes]
- Current adapter: `agentteams/frameworks/goose.py` (`_emit_recipe`, `_validate_recipe_yaml`); validator wiring `agentteams/cli/recipe_check.py`.

[rr]: https://block.github.io/goose/docs/guides/recipes/recipe-reference/
[subrecipes]: https://block.github.io/goose/docs/guides/recipes/sub-recipes/
