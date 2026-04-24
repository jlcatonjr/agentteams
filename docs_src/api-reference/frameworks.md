# `frameworks` — AgentTeamsModule

Per-framework adapter classes that control how rendered agent content is adjusted for a specific target framework.

> *Source: `agentteams/frameworks/`*

---

## `FrameworkAdapter` (Abstract Base Class)

> *Source: `agentteams/frameworks/base.py`*

Abstract interface for per-framework agent file generation. All concrete adapters inherit from this class.

### Abstract Properties

#### `framework_id`

Short identifier for this framework (e.g., `'copilot-vscode'`).

**Type:** `str`

### Abstract Methods

#### `render_agent_file(content, agent_slug, manifest)`

Post-process rendered agent content for this framework.

**Args:**

- `content` (`str`) — Rendered agent file content (placeholders already resolved).
- `agent_slug` (`str`) — Agent slug derived from the filename.
- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.

**Returns:** `str` — Framework-adjusted agent content.

---

#### `render_instructions_file(content, manifest)`

Post-process rendered `copilot-instructions` content.

**Args:**

- `content` (`str`) — Rendered instructions content.
- `manifest` (`dict[str, Any]`) — Team manifest.

**Returns:** `str` — Framework-adjusted instructions content.

---

#### `get_file_extension(file_type)`

Return the file extension for a given file type.

**Args:**

- `file_type` (`str`) — `'agent'`, `'instructions'`, or `'builder'`.

**Returns:** `str` — Extension string including the dot (e.g., `'.agent.md'`).

---

#### `supports_handoffs()`

Whether this framework supports YAML handoff blocks in agent files.

**Returns:** `bool`

---

#### `handoff_delivery_mode()`

Describe how the framework receives handoff semantics.

- `native` keeps handoffs inline in the emitted agent file.
- `manifest` strips inline handoff syntax from the visible prompt and, when extracted handoffs exist, preserves routing metadata in `references/runtime-handoffs.json`.
- `none` means no handoff delivery mechanism is emitted.

**Returns:** `str`

---

#### `extract_handoffs(content)`

Extract handoff metadata from rendered agent content before adapter-specific stripping occurs.

For the built-in adapters, this reads YAML `handoffs:` entries and the conventional `## Handoff Instructions` body section.

**Args:**

- `content` (`str`) — Rendered agent file content before framework stripping.

**Returns:** `list[dict[str, Any]]`

---

#### `get_agents_dir(project_path)`

Return the default agent file directory for a given project path.

**Args:**

- `project_path` (`Path`) — Root of the target project.

**Returns:** `Path`

---

### Concrete Method

#### `finalize_output_path(rel_path, file_type)`

Adjust an output path's extension for this framework. Default implementation: no-op.

**Args:**

- `rel_path` (`str`) — Relative output path.
- `file_type` (`str`) — Logical file type (`agent`, `builder`, `instructions`, etc.).

**Returns:** `str` — Path with adjusted extension.

---

## `CopilotVSCodeAdapter`

> *Source: `agentteams/frameworks/copilot_vscode.py`*

Adapter for GitHub Copilot in VS Code.

- **framework_id:** `'copilot-vscode'`
- **Output format:** `.agent.md` with YAML front matter
- **Handoffs:** Native inline YAML
- **Agents dir:** `<project>/.github/agents/`

Validates and normalizes YAML front matter; preserves all fields defined in the template.

---

## `CopilotCLIAdapter`

> *Source: `agentteams/frameworks/copilot_cli.py`*

Adapter for Copilot CLI.

- **framework_id:** `'copilot-cli'`
- **Output format:** Plain `.md` system prompts
- **Handoffs:** Runtime manifest when handoffs are present (`references/runtime-handoffs.json`)
- **Agents dir:** `<project>/.github/copilot/`

Strips YAML front matter and inline handoff blocks to produce plain Markdown system prompts compatible with the Copilot CLI, while preserving extracted handoff metadata in `references/runtime-handoffs.json` when any handoffs are present.

---

## `ClaudeAdapter`

> *Source: `agentteams/frameworks/claude.py`*

Adapter for Claude Projects.

- **framework_id:** `'claude'`
- **Output format:** Claude front matter `.md` (`CLAUDE.md`-compatible)
- **Handoffs:** Runtime manifest when handoffs are present (`references/runtime-handoffs.json`)
- **Agents dir:** `<project>/.claude/agents/`

Strips VS Code YAML and inline handoff blocks, then injects Claude-compatible front matter and preserves Markdown body content. Extracted handoff metadata is emitted separately in `references/runtime-handoffs.json` when any handoffs are present.
