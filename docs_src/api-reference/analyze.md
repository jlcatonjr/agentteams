# `analyze` — AgentTeamsModule

Analyze a project description to produce a team manifest.

Takes the normalized description dict from [`ingest.load()`](ingest.md) and produces a team manifest dict conforming to `schemas/team-manifest.schema.json`.

> *Source: `agentteams/analyze.py`*

---

## Functions

### `build_manifest(description, *, framework='copilot-vscode')`

> *Source: `agentteams/analyze.py`*

Build and return a team manifest from a normalized project description.

**Args:**

- `description` (`dict[str, Any]`) — Normalized project description from `ingest.load()`.
- `framework` (`str`, keyword-only) — Target agent framework: `'copilot-vscode'`, `'copilot-cli'`, or `'claude'`. Default: `'copilot-vscode'`.

**Returns:** `dict[str, Any]` — Team manifest conforming to `schemas/team-manifest.schema.json`.

**Behavior Notes:**

- If `description` contains `selected_archetypes`, `build_manifest()` uses it as the primary archetype input, then applies required dependency and consistency additions.
- If `post-production-auditor` is selected (auto-selected or override), `technical-validator` is also ensured in the final archetype set.
- If tool metadata is incomplete, `tool-doc-researcher` may be auto-added to support metadata completion.
- Retrieval integration is normalized into a stable manifest contract with defaults (`mode`, entrypoints, trigger sources, source-of-truth, staleness SLO, trigger contract version).
- If normalized retrieval mode is not `none`, `retrieval-integrator` is auto-included when absent.
- When retrieval integration is enabled, output planning adds retrieval reference artifacts:
	- `references/retrieval-integration.reference.md`
	- `references/retrieval-trigger-contract.reference.md`
- `existing_project_path` is propagated to the manifest so downstream artifact builders (for example memory-index source collection) can use the operator's explicit project root.
- `research-analyst` is force-added to the archetype set when `description["capabilities"]` includes `research_verification` — an explicit opt-in, never inferred from project type, since it pulls in the `agentteams[research]` runtime dependency. Applied after both the auto-select and `selected_archetypes`-override paths, so an override cannot silently drop it. When `project_type` is `'research'` and this capability is absent, the manifest instead records a `research-capability-unset` advisory (does not auto-enable).
- `agentteams-updater` is force-added the same way when `description["capabilities"]` includes `instance_maintenance`.
- When the description declares `mcp_hints`, detected MCP server candidates are added to the manifest as `mcp_candidates`; operator-declared `mcp_servers` are copied through to the manifest verbatim (with schema defaults normalized).
- Operator-declared Goose-native `recipe_parameters`, `recipe_response`, and `recipe_retry` are copied through to the manifest when present and valid (opt-in; manifests that declare none are unaffected).

---

### `classify_project_type(description)`

> *Source: `agentteams/analyze.py`*

Return a project type string based on keyword analysis of the description.

**Args:**

- `description` (`dict[str, Any]`) — Normalized project description.

**Returns:** `str` — One of `'writing'`, `'software'`, `'data-pipeline'`, `'research'`, `'documentation'`, `'mixed'`, or `'unknown'`.

---

### `select_archetypes(description)`

> *Source: `agentteams/analyze.py`*

Select and return the list of domain agent archetype slugs appropriate for the project.

**Args:**

- `description` (`dict[str, Any]`) — Normalized project description.

**Returns:** `list[str]` — Ordered list of archetype slugs (e.g., `['primary-producer', 'quality-auditor', 'technical-validator']`).

**Selection Notes:**

- `module-doc-author` and `module-doc-validator` are always selected together as a pair, gated on a tight, package-exclusive keyword set (`pypi`, `mkdocs`, `sphinx`, `readthedocs`, `sdist`) — any single occurrence is decisive. Weak words like bare `package`/`distribution`/`install` do not trigger the pair.
- `post-production-auditor` is selected using contextual co-occurrence cues, not single keyword hits.
- Auto-selection requires at least one operation/state-change cue plus at least one verification/proof cue.
- Legacy pipeline cues (`pipeline`, `etl`, `collector`) still work when paired with verification/proof cues.
- Matching uses boundary-aware keyword detection to avoid substring collisions (for example, `sync` does not match inside `async`).

---

### `classify_tool_importance(tool)`

> *Source: `agentteams/analyze.py`*

Classify a single tool dict as `'specialist'`, `'reference'`, or `'passive'`.

**Args:**

- `tool` (`dict[str, Any]`) — Tool dict with at minimum a `name` key.

**Returns:** `str` — `'specialist'` (operational tool doc: reference on Copilot, skill on Claude), `'reference'` (lightweight reference file), or `'passive'` (no dedicated artifact). Tools are never generated as agents.

**Behavior Notes:**

- If `tool.get("needs_specialist_agent") is True`, the tool is unconditionally classified `'specialist'` before any category/name heuristic runs. `False` or an absent key falls through to the category/name heuristics (it does not force a non-specialist tier).

---

### `detect_tool_agents(tools)`

> *Source: `agentteams/analyze.py`*

Return specs for operational tool *documents* (reference docs / Claude skills). Name retained for backward compatibility; tools are never agents.

**Args:**

- `tools` (`list[dict[str, Any]]`) — List of tool dicts from the project description.

**Returns:** `list[dict[str, Any]]` — Operational tool-doc specs (not raw input tool dicts), each including `slug`, `tool_name`, `tool_version`, `tool_category`, `config_files`, `invocation_command`, `invocation_target`, `docs_url`, `api_surface`, and `common_patterns`.

---

### `detect_reference_tools(tools)`

> *Source: `agentteams/analyze.py`*

Return tool dicts classified as reference-tier (informational, no dedicated agent).

**Args:**

- `tools` (`list[dict[str, Any]]`) — List of tool dicts from the project description.

**Returns:** `list[dict[str, Any]]` — Reference-tier tool specs (not raw input tool dicts), each including `slug`, `tool_name`, `tool_version`, `tool_category`, `config_files`, `docs_url`, `api_surface`, and `common_patterns`.

---

### `build_authority_hierarchy(description)`

> *Source: `agentteams/analyze.py`*

Build the authority hierarchy list from the project description's `authority_sources` field.

**Args:**

- `description` (`dict[str, Any]`) — Normalized project description.

**Returns:** `list[dict[str, Any]]` — Ordered authority source dicts with `rank`, `name`, `path`, and `scope` keys.

---

### `adopt_orphan_agents(manifest, orphan_slugs) -> list[str]`

> *Source: `agentteams/analyze.py`*

Register pre-existing ("orphan") agent files into the team roster.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest to update in place.
- `orphan_slugs` (`list[str]`) — Slugs of pre-existing agent files not already tracked by the manifest.

**Returns:** `list[str]` — Slugs newly adopted (excludes any already present in `agent_slug_list`).

**Behavior Notes:**

- Adds each newly-adopted slug to `agent_slug_list` (so the orchestrator declares it as a handoff target) and to `adopted_agents`, and re-renders the `AGENT_SLUG_LIST` placeholder.
- Deliberately does NOT add adopted slugs to `domain_agent_slugs` — they are not standard domain archetypes and carry no auto-generated routing/trigger metadata.
- Deliberately does NOT touch `output_files` — adopted agents' own files are never generated or overwritten, preserving their bespoke content.

---

## Retrieval Contract Normalization

`build_manifest()` normalizes retrieval integration before placeholder resolution and output planning.

### Defaults when input is missing/invalid

- `mode`: `none`
- `query_entrypoints`: `[]`
- `maintenance_entrypoints`: `[]`
- `trigger_sources`: `['manual']`
- `source_of_truth`: `[]`
- `staleness_slo_minutes`: `60`
- `trigger_contract_version`: `v1`

### Normalization guarantees

- Unknown retrieval modes are coerced to `none`.
- Non-list entrypoint/source fields are sanitized to empty lists.
- Empty trigger-source lists are normalized to `['manual']`.
- Invalid staleness values fall back to `60`.

These guarantees stabilize downstream rendering and schema validation behavior.

---

## See also

- [`manifest_format`](manifest-format.md) — the `_format_*` / `_default_*` field-derivation helpers `build_manifest` assembles into the manifest.
- [`render`](render.md) — consumes the team manifest `build_manifest` produces.
