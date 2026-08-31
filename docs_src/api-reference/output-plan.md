# `output_plan` — Planning the Emitted File Set

> *Source: `agentteams/output_plan.py`*

Decides **which files a team manifest produces**, before anything is rendered. Every
agent, tool doc, reference file, instructions file and builder artifact appears here
first.

Extracted from [`analyze.py`](analyze.md) under CH-07. `analyze` re-exports `_plan_output_files`, so
`analyze._plan_output_files` resolves unchanged. The three `analyze`-owned symbols it
needs (`GOVERNANCE_AGENTS`, `ALWAYS_INCLUDED_DOMAIN_AGENTS`, `_dedupe_keep_order`) are
imported **lazily inside the function** to keep the module graph acyclic — `analyze`
imports this module at load time, so a module-level import back would be a cycle.

## API

### `_plan_output_files(archetypes, tool_agents, reference_tools, components, framework)`

Plan the list of files the [emit](emit.md) phase will generate.

**Args:**

- `archetypes` (`list[str]`) — Selected domain archetype slugs.
- `tool_agents` (`list[dict[str, Any]]`) — Specialist-tier tool specs. Each entry's
  `tool_category` selects `domain/tool-<category>.doc.template.md`, with
  `domain/tool-specific.doc.template.md` as the fallback when that category has no
  template — which is how an unknown category degrades instead of failing.
- `reference_tools` (`list[dict[str, Any]]`) — Reference-tier tool specs.
- `components` (`list[dict[str, Any]]`) — Project components; each yields one
  workstream-expert agent.
- `framework` (`str`) — Target framework. Changes the instructions path
  (`../CLAUDE.md` vs `../copilot-instructions.md`), the tool-doc layout (Claude emits
  flat skills under `../skills/`; others emit `references/ref-*-reference.md`), and the
  builder template.

**Returns:** `list[dict[str, Any]]` — One entry per planned file, with keys `path`,
`template`, `type`, `component_slug`, and optionally `fallback_template` / `tool_slug`.

An entry with an **empty** `template` marks a post-render artifact — the pipeline
graph, its SVGs, `SETUP-REQUIRED.md` — produced by `build_team.py` rather than the
[renderer](render.md).

## Every template must be reachable from here

`agentteams/templates/` is packaged wholesale, so a template no output plan reaches
still ships in the wheel and still gets swept by [drift detection](drift.md) — it just never
renders. Nothing detected that: the orphan advisory
(`build_team._report_orphan_reference_docs`) runs on the *output* side, finding emitted
files the plan no longer produces, which is the opposite direction.

`tests/test_template_emission_coverage.py` closes it, and its first version **passed a
planted orphan** because it derived the valid archetype set from the templates
directory — making every file that existed trivially "reachable". Archetypes and tool
categories are now read from `analyze.py` instead.

Three templates are allowlisted with a reason: the `*.csv.template` header files, which
`liaison_logs.py` records as having zero readers, where the `*_HEADERS` constants are
the source of truth.
