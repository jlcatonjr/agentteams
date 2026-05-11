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

**Returns:** `str` — `'specialist'` (gets a dedicated tool agent), `'reference'` (gets a reference file), or `'passive'` (no dedicated artifact).

---

### `detect_tool_agents(tools)`

> *Source: `agentteams/analyze.py`*

Return tool dicts classified as requiring a dedicated tool-specialist agent.

**Args:**

- `tools` (`list[dict[str, Any]]`) — List of tool dicts from the project description.

**Returns:** `list[dict[str, Any]]` — Specialist-tier tool agent specs (not raw input tool dicts), each including `slug`, `tool_name`, `tool_version`, `tool_category`, `config_files`, `invocation_command`, `invocation_target`, `docs_url`, `api_surface`, and `common_patterns`.

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
