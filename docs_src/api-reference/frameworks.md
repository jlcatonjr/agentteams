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

This is the adapter step that turns the framework-agnostic output of `render.render_all()` into the final framework-specific file body.

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

### Concrete Methods

#### `finalize_output_path(rel_path, file_type)`

Adjust an output path's extension for this framework. Default implementation: no-op.

**Args:**

- `rel_path` (`str`) — Relative output path.
- `file_type` (`str`) — Logical file type (`agent`, `builder`, `instructions`, etc.).

**Returns:** `str` — Path with adjusted extension.

#### `render_builder_file(content, manifest)`

Post-process the rendered team-builder meta-agent. **Default implementation: identity** (returns `content` unchanged), so Copilot/Claude emit the builder as a Markdown agent file. Frameworks whose agent files are not Markdown override this — `GooseAdapter` wraps the builder as a runnable recipe so it is not a stray `.md` in the agents directory.

**Args:**

- `content` (`str`) — Rendered builder template body.
- `manifest` (`dict`) — Team manifest.

**Returns:** `str` — Framework-shaped builder content.

#### `extra_output_files(manifest)`

Return additional `(rel_path, content)` files the framework emits that are not derived from a template. **Default implementation: empty list.** `GooseAdapter` overrides this to emit the repo-root `.goosehints` integrator alongside `AGENTS.md`. These files are emitted by the generate path **and** by `convert_team`, so a converted Goose team also gets its `.goosehints`.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest.

**Returns:** `list[tuple[str, str]]` — `(rel_path, content)` pairs, relative to the agents directory.

---

## `CopilotVSCodeAdapter`

> *Source: `agentteams/frameworks/copilot_vscode.py`*

Adapter for GitHub Copilot in VS Code.

- **framework_id:** `'copilot-vscode'`
- **Output format:** `.agent.md` with YAML front matter
- **Handoffs:** Native inline YAML
- **Agents dir:** `<project>/.github/agents/`

Validates and normalizes YAML front matter; preserves all fields defined in the template.

**Current behavior notes:**

- Normalizes front matter while filtering `agents` references to generated team members.
- Supports both `agents:` flow-list and block-list syntax.
- Filters handoff targets to generated team members.
- Preserves original formatting when membership is unchanged to avoid no-op cosmetic drift in generated outputs.

---

## `CopilotCLIAdapter`

> *Source: `agentteams/frameworks/copilot_cli.py`*

Adapter for Copilot CLI.

- **framework_id:** `'copilot-cli'`
- **Output format:** Plain `.md` system prompts
- **Handoffs:** Runtime manifest when handoffs are present (`references/runtime-handoffs.json`)
- **Agents dir:** `<project>/.github/copilot/`

Strips YAML front matter and inline handoff blocks to produce plain Markdown system prompts compatible with the Copilot CLI, while preserving extracted handoff metadata in `references/runtime-handoffs.json` when any handoffs are present.

**Current behavior notes:**

- Handoff extraction happens before stripping so routing metadata can be persisted for runtime use.
- `handoff_delivery_mode()` is `manifest`, meaning routing metadata is delivered via `references/runtime-handoffs.json` rather than inline YAML.

---

## `ClaudeAdapter`

> *Source: `agentteams/frameworks/claude.py`*

Adapter for Claude Projects.

- **framework_id:** `'claude'`
- **Output format:** Claude front matter `.md` (`CLAUDE.md`-compatible)
- **Handoffs:** Runtime manifest when handoffs are present (`references/runtime-handoffs.json`)
- **Agents dir:** `<project>/.claude/agents/`

Strips VS Code YAML and inline handoff blocks, then injects Claude-compatible front matter and preserves Markdown body content. Extracted handoff metadata is emitted separately in `references/runtime-handoffs.json` when any handoffs are present.

**Current behavior notes:**

- Uses manifest-based handoff delivery (`references/runtime-handoffs.json`) for routing semantics.
- Performs framework-specific output shaping while preserving rendered prompt body intent.

---

## `GooseAdapter`

> *Source: `agentteams/frameworks/goose.py`*

!!! note "Beta"
    The `GooseAdapter` is in **beta**: generate, convert, and bridge are supported and validated against the Goose CLI, but interop-to-Goose is not yet supported and convert from `claude`/`copilot-cli` sources currently yields flat recipes. Its API and emitted-artifact shapes are **not yet covered** by the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md) and may change in a minor release.

Adapter for Block / AAIF Goose recipes.

- **framework_id:** `'goose'`
- **Output format:** Goose recipe YAML (`.goose/recipes/*.yaml`), schema version `1.0.0`
- **Handoffs:** Native, encoded inline in the recipes — orchestrator handoffs become `sub_recipes` (with the `summon` platform extension); every deeper edge becomes a `summon` `load("<slug>")` reference (Goose forbids nested delegation). **No** `references/runtime-handoffs.json` sidecar.
- **Agents dir:** `<project>/.goose/recipes/`
- **Instructions:** the team brief is written to the repo-root `AGENTS.md` (via `finalize_output_path`), and a `.goosehints` integrator (`@AGENTS.md` + operational notes) is emitted via `extra_output_files`.

Transforms each rendered Markdown agent into a recipe (`title`/`description`/`instructions`/`extensions`/optional `sub_recipes`). The team-builder is wrapped as a runnable `team-builder.yaml` recipe via the `render_builder_file` hook. `get_file_extension('agent')` and `'builder'` both return `.yaml`.

**Current behavior notes:**

- `supports_handoffs()` is `True`; `handoff_delivery_mode()` is `'native'`.
- One delegation layer only (Goose constraint); deeper structure is preserved as in-context `summon` `load(...)` references, not nested delegations.

---

## Runtime Handoff Artifact Contract

Adapters using manifest-based handoff delivery (`CopilotCLIAdapter`, `ClaudeAdapter`) rely on extracted handoff metadata generated from rendered content and emitted as `references/runtime-handoffs.json` when handoffs exist. Native-handoff adapters (`CopilotVSCodeAdapter`, `GooseAdapter`) keep handoff semantics inline (for Goose, encoded directly in the recipes).
