# `analyze` — AgentTeamsModule

Analyze a project description to produce a team manifest.

Takes the normalized description dict from `ingest.load()` and produces a team manifest dict conforming to `schemas/team-manifest.schema.json`.

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
