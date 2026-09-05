# `analyze_tools` — AgentTeamsModule

Tool importance classification and tool/reference-doc detection.

A CH-07 carve-out of [`analyze`](analyze.md): the cohesive "which tools earn a specialist agent, an operational tool doc, or a reference-DB entry" concern, extracted from `analyze.py` to keep it under the module-size ceiling. `analyze.py` re-exports these where its callers expect them.

> *Source: `agentteams/analyze_tools.py`*

---

## Functions

### `classify_tool_importance(tool)`

> *Source: `agentteams/analyze_tools.py`*

Classify a tool into an importance tier.

**Args:**

- `tool` — Tool dict with at least `name`, optionally `category` and `needs_specialist_agent`.

**Returns:** one of `'specialist'`, `'reference'`, or `'passive'`.

---

### `detect_tool_agents(tools)`

> *Source: `agentteams/analyze_tools.py`*

Return operational tool-doc specs for specialist-tier tools.

Specialist-tier tools (databases, CLIs, build systems, infra) become operational documents — Claude skills or Copilot reference docs — never agents. The spec slug stays `tool-<name>` to identify the tool.

**Args:**

- `tools` — List of tool dicts from the project description.

**Returns:** a list of tool-doc spec dicts for specialist-tier tools.

---

### `detect_reference_tools(tools)`

> *Source: `agentteams/analyze_tools.py`*

Return tool specs for tools classified as reference-tier.

**Args:**

- `tools` — List of tool dicts from the project description.

**Returns:** a list of tool dicts for reference-tier tools.
