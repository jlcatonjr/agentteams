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

#### `get_agents_dir(project_path)`

Return the default agent file directory for a given project path.

**Args:**

- `project_path` (`Path`) — Root of the target project.

**Returns:** `Path`

---

### Concrete Method

#### `finalize_output_path(rel_path)`

Adjust an output path's extension for this framework. Default implementation: no-op.

**Args:**

- `rel_path` (`str`) — Relative output path.

**Returns:** `str` — Path with adjusted extension.

---

## `CopilotVSCodeAdapter`

> *Source: `agentteams/frameworks/copilot_vscode.py`*

Adapter for GitHub Copilot in VS Code.

- **framework_id:** `'copilot-vscode'`
- **Output format:** `.agent.md` with YAML front matter
- **Handoffs:** Supported
- **Agents dir:** `<project>/.github/agents/`

Validates and normalizes YAML front matter; preserves all fields defined in the template.

---

## `CopilotCLIAdapter`

> *Source: `agentteams/frameworks/copilot_cli.py`*

Adapter for Copilot CLI.

- **framework_id:** `'copilot-cli'`
- **Output format:** Plain `.md` system prompts
- **Handoffs:** Not supported (YAML and handoff blocks stripped)
- **Agents dir:** `<project>/.github/agents/`

Strips YAML front matter and handoff blocks to produce plain Markdown system prompts compatible with the Copilot CLI.

---

## `ClaudeAdapter`

> *Source: `agentteams/frameworks/claude.py`*

Adapter for Claude Projects.

- **framework_id:** `'claude'`
- **Output format:** Plain `.md` (`CLAUDE.md`-compatible)
- **Handoffs:** Not supported
- **Agents dir:** `<project>/.github/agents/`

Strips YAML and handoff blocks; produces plain Markdown suitable for use as Claude system prompts.
