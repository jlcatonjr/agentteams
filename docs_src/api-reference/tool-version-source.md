# `tool_version_source` — AgentTeamsModule

Tool versions read from the target project's pin file.

A brief's `tools[].version` is fixed at generation time. For a tool the target project pins in a file of its own (a Lean project's `lean/lean-toolchain`, for example), an optional `version_source` on the tool entry names where the pin lives, and `docs_url` may carry `{version}` / `{major_minor}`. The rendered tool document then tells the agent to resolve the version from the pin file (or the resolver) at use time, and labels the brief's `version` as the generation-time default. Without `version_source`, rendered output is unchanged.

```json
"version_source": {
  "file": "lean/lean-toolchain",
  "pattern": "v(?P<version>[0-9][0-9.]*)",
  "resolver": "mathagents run lean_docs"
}
```

`file` and `pattern` are required; `pattern` must define the named group `version`; `resolver` is optional and is run by the agent, never by agentteams.

> *Source: `agentteams/tool_version_source.py`*

---

## Functions

### `resolve_tool_version(tool, project_root)`

> *Source: `agentteams/tool_version_source.py`*

Read a tool's pinned version from the target project's pin file. Pure: one file read, no command, no network.

**Args:**

- `tool` — A `tools[]` entry (or a tool-doc spec carrying `version_source`).
- `project_root` — The target project's root directory.

**Returns:** the `version` group of the first match, or `None` when there is no `version_source`, the file is missing, unreadable or outside `project_root`, or the pattern does not match.

---

### `version_source_errors(tool)`

> *Source: `agentteams/tool_version_source.py`*

Validation errors for a tool entry's `version_source` (called by `ingest.validate`): a missing or non-relative `file`, a `pattern` that does not compile or lacks the `version` group, an empty `resolver`, or unknown keys.

**Returns:** a list of error strings; `[]` when absent or valid.

---

### `expand_docs_url(docs_url, version)`

> *Source: `agentteams/tool_version_source.py`*

Substitute `{version}` and `{major_minor}` (first two dot-separated components) in a docs URL. Unchanged when it has neither or `version` is empty.

---

### `tool_version_placeholders(spec, docs_url)`

> *Source: `agentteams/tool_version_source.py`*

The `TOOL_VERSION`, `TOOL_DOCS_URL` and `TOOL_VERSION_RESOLUTION` placeholder values for one tool doc, used by `render`. Without `version_source`, `TOOL_VERSION_RESOLUTION` is empty and the other two keep their previous values.
