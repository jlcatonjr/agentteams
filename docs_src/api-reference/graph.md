# `graph` — AgentTeamsModule

Directed graph inference for agent team topology.

Parses generated agent files (from in-memory rendered content or disk) to build a directed graph of the agent team. Each node is an agent; each edge is a declared connection via YAML `handoffs:` or `agents:` list entries.

Outputs a standalone **SVG** diagram plus Mermaid flowchart, DOT (Graphviz) source, JSON adjacency list, and a human-readable Markdown document. The Markdown document (`references/pipeline-graph.md`) references the sibling SVG (`references/pipeline-graph.svg`) as its primary diagram and keeps the Mermaid/DOT source in a collapsed `<details>` block. Both are regenerated automatically on every `build_team.py` run.

> *Source: `agentteams/graph.py`*

---

## Classes

### `AgentNode`

> *Source: `agentteams/graph.py`*

Metadata for a single agent node in the team graph.

**Attributes:**

- `slug` (`str`) — Machine-readable identifier derived from filename.
- `display_name` (`str`) — Human-readable name from YAML `name:` field.
- `agent_type` (`str`) — Categorical type: `'governance'`, `'domain'`, `'workstream_expert'`, `'tool_specialist'`, or `'unknown'`.
- `user_invokable` (`bool`) — `True` if the agent can be invoked directly by a user.
- `tools` (`list[str]`) — Declared tool list from YAML.

---

### `GraphEdge`

> *Source: `agentteams/graph.py`*

A directed edge between two agent nodes.

**Attributes:**

- `source` (`str`) — Slug of the originating agent.
- `target` (`str`) — Slug of the target agent.
- `edge_type` (`str`) — `'handoff'` (from `handoffs:` YAML block) or `'agents-list'` (from `agents:` YAML list).
- `label` (`str | None`) — Optional human-readable label from handoff `label:` key.

---

### `TeamGraph`

> *Source: `agentteams/graph.py`*

Complete directed graph of the agent team.

**Attributes:**

- `project_name` (`str`) — Name of the project this graph belongs to.
- `nodes` (`dict[str, AgentNode]`) — Dict mapping slug → `AgentNode`.
- `edges` (`list[GraphEdge]`) — List of directed `GraphEdge` instances.

**Methods:**

#### `adjacency()`

Return a dict mapping each slug to its list of direct successors.

**Returns:** `dict[str, list[str]]` — Adjacency dict; values are sorted lists of target slugs.

#### `reverse_adjacency()`

Return a dict mapping each slug to its list of direct predecessors.

**Returns:** `dict[str, list[str]]` — Reverse adjacency dict; values are sorted lists of source slugs.

#### `to_json()`

Serialise the graph to a JSON adjacency list.

**Returns:** `str` — JSON string with keys: `project_name`, `nodes`, `edges`, `adjacency`.

#### `to_mermaid()`

Render the graph as a Mermaid `flowchart LR`. Nodes are colour-coded by agent type. Handoff edges are solid arrows; agents-list edges are dashed.

**Returns:** `str` — Mermaid `flowchart LR` block (no surrounding code fences).

#### `to_dot()`

Render the graph as a Graphviz DOT source file.

**Returns:** `str` — DOT digraph source as a plain string.

#### `to_svg()`

Render the graph as a standalone, deterministic SVG document (stdlib layered layout with barycenter crossing reduction; no Graphviz/Mermaid runtime dependency). It is byte-identical for identical input so the pre-commit hook never churns it.

**Returns:** `str` — SVG XML as a plain string.

#### `to_handoff_svg()`

Render the handoff-only control-flow backbone as SVG — the same deterministic layout engine as `to_svg()`, but with `agents-list` edges omitted so only `handoffs:` edges remain. This is the diagram referenced as `pipeline-handoffs.svg` by the Markdown document.

**Returns:** `str` — SVG XML as a plain string.

#### `to_markdown_document()`

Render a full Markdown document (structured as described in the module summary above), plus a legend and agent tables. Written to `references/pipeline-graph.md` on every `build_team.py` run.

**Returns:** `str` — Complete Markdown document as a string.

---

## Functions

### `build_graph(file_map, project_name='')`

> *Source: `agentteams/graph.py`*

Build a `TeamGraph` from in-memory rendered agent file content.

**Args:**

- `file_map` (`dict[str, str]`) — Dict mapping relative path → rendered content.
- `project_name` (`str`) — Display name for the project. Default: `''`.

**Returns:** `TeamGraph`

---

### `generate_graph_document(file_map, project_name='')`

> *Source: `agentteams/graph.py`*

Generate the full Markdown graph document combining Mermaid, DOT, and JSON representations.

**Args:**

- `file_map` (`dict[str, str]`) — Dict mapping relative path → rendered content.
- `project_name` (`str`) — Project display name for the document header. Default: `''`.

**Returns:** `str` — Full Markdown document content.

---

### `generate_graph_svg(file_map, project_name='')`

> *Source: `agentteams/graph.py`*

Build the graph and return the full agent-topology SVG document (equivalent to `build_graph(...).to_svg()`).

**Args:**

- `file_map` (`dict[str, str]`) — Dict mapping relative path → rendered content.
- `project_name` (`str`) — Display name for the project. Default: `''`.

**Returns:** `str` — SVG XML as a plain string.

---

### `generate_graph_handoff_svg(file_map, project_name='')`

> *Source: `agentteams/graph.py`*

Build the graph and return the handoff-only backbone SVG document (equivalent to `build_graph(...).to_handoff_svg()`).

**Args:**

- `file_map` (`dict[str, str]`) — Dict mapping relative path → rendered content.
- `project_name` (`str`) — Display name for the project. Default: `''`.

**Returns:** `str` — SVG XML as a plain string.

---

### `load_from_disk(agents_dir)`

> *Source: `agentteams/graph.py`*

Load all `.agent.md` files from an agents directory into a `file_map`. Shared by the CLI entry point and the commit-triggered refresh in [`git_hooks`](git-hooks.md). Skips dot-prefixed subdirectories (e.g. `.agentteams-backups/`) so backup/ghost agents are not parsed as live nodes.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` (or `.claude/agents/`) directory.

**Returns:** `dict[str, str]` — Dict mapping relative path → file content.

---

### `infer_project_name(file_map)`

> *Source: `agentteams/graph.py`*

Infer a project name from agent `name:` fields. Agent names follow `<Role> — <Project>` (em dash) or `<Role> - <Project>` (hyphen); the trailing segment is the project name. Shared by the CLI entry point and the commit-triggered refresh in [`git_hooks`](git-hooks.md) so both derive the same heading.

**Args:**

- `file_map` (`dict[str, str]`) — Dict mapping relative path → rendered content.

**Returns:** `str` — The inferred project name, or `''` when no agent carries a splittable name.

---

### `main(argv=None)`

> *Source: `agentteams/graph.py`*

CLI entry point for standalone graph generation.

```bash
python -m agentteams.graph /path/to/.github/agents/
python -m agentteams.graph /path/to/.github/agents/ --format mermaid
python -m agentteams.graph /path/to/.github/agents/ --format dot
python -m agentteams.graph /path/to/.github/agents/ --format json
python -m agentteams.graph /path/to/.github/agents/ --format svg
python -m agentteams.graph /path/to/.github/agents/ --output pipeline-graph.md
```

**Args:**

- `argv` (`list[str] | None`) — Argument list. If `None`, uses `sys.argv[1:]`.

**Returns:** `int` — Exit code (0 = success, 1 = error).

---

## See also

- [`architecture`](architecture.md) — the code-side sibling that maps module dependencies; this map covers agent topology.
- [`emit`](emit.md) — the pipeline stage that writes the rendered agent files this graph is inferred from.
- [CLI reference](../cli-reference.md) — `agentteams --refresh-graph` regenerates this map on demand.
