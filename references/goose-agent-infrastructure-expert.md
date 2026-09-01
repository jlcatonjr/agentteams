# Goose Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating Block/AAIF **Goose** recipe
infrastructure into AgentTeamsModule. Authored 2026-08-15
(agent-doc-optimal-structure plan; closes the goose gap in the per-framework
expert-reference set — parity report R6).

## Authoritative Documentation (verified live 2026-08-15)

Goose was donated to the Linux Foundation's Agentic AI Foundation (April 2026);
repo moved `block/goose` → `aaif-goose/goose`; docs moved to goose-docs.ai.
`block.github.io/goose/...` URLs are dead.

- Recipe reference: https://goose-docs.ai/docs/guides/recipes/recipe-reference/
- Goosehints / context files: https://goose-docs.ai/docs/guides/context-engineering/using-goosehints/
- AAIF move announcement: https://goose-docs.ai/blog/2026/04/07/goose-moves-to-aaif/
- Project: https://github.com/aaif-goose/goose · https://aaif.io/projects/goose

## Verified Upstream Conventions (2026-08-15)

- **Recipe schema.** Required: `title`, `description`, and at least one of
  `instructions` OR `prompt`. `prompt` is the task message and is required for
  headless (non-interactive) runs; `instructions` is system-level guidance.
  Optional: `version`, `parameters` (typed; defaults mandatory for optional
  params), `extensions`, `settings` (goose_provider/goose_model/temperature/
  max_turns), `activities`, `retry`, `response.json_schema`, `sub_recipes`.
- **Sub-recipes.** `{name, path, values, sequential_when_repeated, description}`;
  each becomes a generated tool; isolated sessions; **sub-recipes cannot define
  their own sub_recipes — one layer max is the platform rule** (our adapter's
  one-layer cap is therefore conformant, not a private limitation).
- **Recipe discovery:** current directory → `GOOSE_RECIPE_PATH` dirs →
  `GOOSE_RECIPE_GITHUB_REPO`. `.goose/recipes/` is NOT a documented discovery
  location. `goose recipe validate` enforces schema + parameter consistency.
- **Context files.** Default `CONTEXT_FILE_NAMES = ["AGENTS.md", ".goosehints"]`,
  loaded hierarchically cwd→root plus subdirectory auto-discovery; global hints at
  `~/.config/goose/.goosehints`; `@file.md` import syntax supported.

## Canonical Output Conventions (ours, current)

- Recipe files: `.goose/recipes/<slug>.yaml` (title, description, instructions,
  extensions, sub_recipes); orchestrator carries sub_recipes; deeper handoffs
  flattened to text references.
- Repo root: `AGENTS.md` (shared with agents-md/codex adapters) + `.goosehints`.

## Known Deltas vs Our Adapter (`agentteams/frameworks/goose.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| G1 | Docs/repo home moved to AAIF; no dead URLs found in `agentteams/` (adapter header already says "Block / AAIF"), but `references/agent-provider-docs.reference.md` carried a dead block.github.io row. | **re-verified** locally | Tranche 1 — register row updated 2026-08-15 |
| G2 | `.goose/recipes/` is not a documented discovery location; invocation needs an explicit path or `GOOSE_RECIPE_PATH`. | researcher-claimed | **Closed 2026-08-15** — discovery order documented above (line 31) and in `agentteams/templates/AUTHORING-GUIDE.md`'s goose section; `goose recipe validate` in CI remains a separate, unscheduled idea |
| G3 | ~~We emit no `prompt:` — recipes can't run headless.~~ **Already shipped, table was stale.** Re-verified 2026-08-15 directly against `agentteams/frameworks/goose.py::_emit_recipe`'s `prompt` parameter (W6) and a live `.goose/recipes/orchestrator.yaml`: every orchestrator recipe carries `prompt: "State your role...."` (`_ORCHESTRATOR_PROBE_PROMPT`), and non-orchestrator agents get one via `_task_prompt`. No code change was needed this pass. | **re-verified** (direct fetch + live file, 2026-08-15) | Closed — was already implemented |
| G4 | Unexploited optional schema surface: sub_recipes `values`/`sequential_when_repeated`/`description`; `retry`; `response.json_schema`; `parameters`; `settings.max_turns`. Optional features, not conformance gaps. | researcher-claimed | Recorded only |
| G5 | ~~Goose natively reads AGENTS.md (default CONTEXT_FILE_NAMES) → our `.goosehints` `@AGENTS.md` import is redundant.~~ **Closed 2026-08-15.** Removed the leading `@AGENTS.md` line from both `goose_docs.py::_goosehints_content` and `bridge.py`'s bridge-entry `.goosehints` generator (the plan's audit found two independent generators carrying the same redundancy, not one). | **re-verified** (default quoted 2026-08-15) | Closed — see `references/agentteams-remediation-log.csv` |

## Integration Checklist

1. Keep the one-layer sub_recipes cap (now platform-confirmed).
2. ~~Tranche 2: add `prompt:` emission for headless support; de-duplicate
   `.goosehints` vs native AGENTS.md; document recipe discovery.~~ **Done
   2026-08-15** — `prompt:` was already shipped (G3); `.goosehints`
   de-duplicated (G5); discovery documented (G2).
3. Point all references at goose-docs.ai URLs.

## Observed Upstream Tokens — `goose` (Daily Pipeline)

Recorded by the daily pipeline on `2026-09-01` from `https://goose-docs.ai/docs/guides/recipes/recipe-reference/`.

- Upstream tokens observed: —
- Upstream locations observed: —
- Fetch status: `ok`
