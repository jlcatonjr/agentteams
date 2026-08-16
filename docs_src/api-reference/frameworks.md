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

#### `get_agents_dir(project_path)`

Return the default agent file directory for a given project path.

**Args:**

- `project_path` (`Path`) — Root of the target project.

**Returns:** `Path`

---

### Concrete Methods

#### `finalize_output_path(rel_path, file_type)`

Adjust an output path's extension for this framework. **Default implementation:** when `file_type` is `agent` or `builder`, rewrites the extension to this framework's `get_file_extension(file_type)` if it differs — e.g. this is what turns `CopilotCLIAdapter`'s planned `.agent.md` paths into plain `.md`. Other file types pass through unchanged by default; `ClaudeAdapter`, `AgentsMdAdapter` (inherited by `CodexAdapter`), and `GooseAdapter` override this further to relocate the `instructions` file to their framework-native root file (`CLAUDE.md`, `AGENTS.md`).

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

Return additional `(rel_path, content)` files the framework emits that are not derived from a template. **Default implementation: empty list.** `GooseAdapter` overrides this to emit three files unconditionally: the repo-root `.goosehints` integrator alongside `AGENTS.md`, a `references/goose-capabilities-reference.md` reference doc, and a repo-root `scripts/goose-run-resilient.py` resilient-runner script (a dead-turn-detecting wrapper around `goose run`, read from this package's own `scripts/` at generation time so the shipped copy can't drift). These files are emitted by the generate path **and** by `convert_team`, so a converted Goose team gets all three too. `ClaudeAdapter` also overrides this, shipping the constitutional PreToolUse hook with every generated Claude team: `../hooks/constitutional-gate.py` (the hook itself) and, when the shipped example asset is present, `../settings.hooks.example.json` (the block an operator merges into their own `settings.json` — never written directly, to avoid clobbering user config).

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest.

**Returns:** `list[tuple[str, str]]` — `(rel_path, content)` pairs, relative to the agents directory.

#### `has_skill_concept()`

Whether this framework has a first-class skill concept. **Default implementation: `False`.** `ClaudeAdapter` overrides it to return `True` (Claude Code's `skills/<slug>/SKILL.md` directories); every other framework emits operational tool docs as reference documents instead.

**Returns:** `bool`

#### `render_skill_file(content, slug, manifest)`

Post-process a rendered operational tool-doc emitted as a skill. **Default implementation: identity** (returns `content` unchanged). Only frameworks with a first-class skill concept invoke this path; `ClaudeAdapter` overrides it to strip stray front matter/handoffs and prepend a minimal skill front-matter block (`name` + `description`), writing the body to `.claude/skills/<slug>/SKILL.md`.

**Args:**

- `content` (`str`) — Rendered operational tool-doc body.
- `slug` (`str`) — Tool-doc slug.
- `manifest` (`dict`) — Team manifest.

**Returns:** `str` — Framework-shaped skill content.

#### `tool_doc_rel_path(slug)`

Project-root-relative placement path for an operational tool doc. **Default implementation:** `references/ref-{base}-reference.md` (stripping a leading `tool-` prefix from `slug`). `ClaudeAdapter` overrides it to `skills/{slug}/SKILL.md`, since Claude Code discovers a skill only as a directory containing `SKILL.md`.

**Args:**

- `slug` (`str`) — Tool-doc slug.

**Returns:** `str`

#### `framework_root_prefix()`

Project-root-relative prefix of this framework's root dir. **Default implementation:** empty string (emitted paths are already project-root relative). `ClaudeAdapter` overrides it to `.claude`, used to build display paths like `.claude/skills/<slug>/SKILL.md` from `tool_doc_rel_path`.

**Returns:** `str`

#### `required_front_matter_keys()`

Front-matter keys every agent file of this framework must declare. **Default implementation:** empty tuple — this framework asserts no front-matter contract. Every adapter overrides this explicitly so "examined, and the answer is none" is distinguishable from "not yet examined": `CopilotVSCodeAdapter` → `(name, description, user-invocable, tools, model)`; `ClaudeAdapter` → `(name, description, tools)`; `CopilotCLIAdapter`, `GooseAdapter`, and `AgentsMdAdapter` → `()` (no front matter at all). `CodexAdapter` inherits `AgentsMdAdapter`'s override.

**Returns:** `tuple[str, ...]`

#### `handoff_delivery_mode()`

Describe how the framework receives handoff semantics.

- `native` keeps handoffs inline in the emitted agent file.
- `manifest` strips inline handoff syntax from the visible prompt and, when extracted handoffs exist, preserves routing metadata in `references/runtime-handoffs.json`.
- `none` means no handoff delivery mechanism is emitted.

**Default implementation:** `"native"` when `supports_handoffs()` is `True`, otherwise `"none"`. `ClaudeAdapter`, `CopilotCLIAdapter`, and `AgentsMdAdapter` (inherited by `CodexAdapter`) override this to return `"manifest"` explicitly, since they strip inline handoffs but still preserve extracted routing metadata.

**Returns:** `str`

#### `extract_handoffs(content)`

Extract handoff metadata from rendered agent content before adapter-specific stripping occurs. **Default implementation:** a concrete parser (~75 lines, not overridden by any built-in adapter) that reads YAML `handoffs:` entries from the front matter and the conventional `## Handoff Instructions` body section, then dedupes by `(agent, prompt)`.

**Args:**

- `content` (`str`) — Rendered agent file content before framework stripping.

**Returns:** `list[dict[str, Any]]`

#### `parse_agent_source(content)`

Framework-native source parse for CAI export. **Default implementation:** returns `None`, telling `interop.export_to_cai` to use its standard Markdown path (front-matter name/description, wrapper-stripped body, capability/handoff extraction per framework). `GooseAdapter` overrides this to parse a recipe YAML directly into CAI export fields (name/description/body, mapped capability tokens, `sub_recipes`/`load(...)` handoffs).

**Args:**

- `content` (`str`) — Rendered agent file content.

**Returns:** `dict[str, Any] | None`

#### `framework_extensions_from_sources(parsed_sources)`

Project-level framework-owned config captured on CAI export. **Default implementation:** returns `{}` (no project-level config). `GooseAdapter` overrides this to aggregate every `parse_agent_source` result's recipe config into the CAI document's `framework_extensions.goose` bucket (builtin extension names, `recipe_parameters`/`recipe_response`/`recipe_retry`).

**Args:**

- `parsed_sources` (`list[dict[str, Any]]`) — Every non-`None` `parse_agent_source` result collected during discovery.

**Returns:** `dict[str, Any]`

#### `apply_framework_extensions(manifest, cai)`

Merge this framework's CAI `framework_extensions` bucket into the import manifest stub. **Default implementation:** no-op (no framework-owned config). `GooseAdapter` overrides this to restore recipe configuration (`recipe_parameters`, `recipe_response`, `recipe_retry`, `recipe_extensions`, `recipe_extensions_mode`) at render time.

**Args:**

- `manifest` (`dict[str, Any]`) — Import manifest stub being built.
- `cai` (`dict[str, Any]`) — The CAI document being imported.

**Returns:** `None`

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
    The `GooseAdapter` is in **beta**: generate, convert, bridge, and interop-to-Goose are all supported and validated against the Goose CLI (see [interop.md](interop.md) for the CAI-to-Goose import path — handoffs render as `sub_recipes`, tool scopes as `recipe_extensions`), but convert from `claude`/`copilot-cli` sources currently yields flat recipes. Its API and emitted-artifact shapes are **not yet covered** by the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md) and may change in a minor release.

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

## `AgentsMdAdapter`

> *Source: `agentteams/frameworks/agents_md.py`*

Adapter for the cross-tool `AGENTS.md` standard.

- **framework_id:** `'agents-md'`
- **Output format:** Agent files: `.agents/<slug>.md` (plain Markdown, no front matter); Instructions: repo-root `AGENTS.md`
- **Handoffs:** Inline handoffs removed from the body; routing preserved in `references/runtime-handoffs.json` (`handoff_delivery_mode()` is `'manifest'`)
- **Agents dir:** `<project>/.agents/`

`AGENTS.md` is an emerging cross-tool standard (Agentic AI Foundation / Linux Foundation, formed Dec 2025) read by many AI coding tools (Continue, Cursor, Cline, OpenAI Codex, Zed, Aider, Gemini CLI, Jules, and more). `--framework agents-md` emits one well-formed, framework-neutral `AGENTS.md` — the team brief: overview, conventions, agent roster + orchestrator routing — as the canonical entry those tools consume, plus the full per-specialist team under `.agents/` for humans and agentteams' own tooling. The standard consumers read only `AGENTS.md`; `.agents/<slug>.md` files are detail files for humans/version-control, so `AGENTS.md` is kept self-sufficient rather than a directory of pointers.

**Current behavior notes:**

- `render_agent_file` strips the VS Code YAML front matter and handoff blocks, rewrites `.github/agents` path references to `.agents`, and prepends a `# {Name}` heading; idempotent on re-render (no compounding duplicate headers).
- `render_instructions_file` neutralizes the Copilot-authored instructions template — strips the leading SECTION-MANIFEST comment, retitles off "Copilot Instructions", rewrites paths, rewords Copilot-specific phrasing — while preserving the AGENTTEAMS fence markers.
- `finalize_output_path` maps the planned instructions path to the repo-root `AGENTS.md`.
- `required_front_matter_keys()` is `()`, stated explicitly rather than left as an unexamined default.
- Shared-namespace note: `--framework goose` also writes a repo-root `AGENTS.md`; if a project uses both, the emitted file carries a one-line generated notice to that effect (see `references/bridge-refresh-safety.md`).
- Generate-only: not a `--convert-from` / `--interop-from` / `--bridge-from` target — the CLI rejects those combinations.

---

## `CodexAdapter`

> *Source: `agentteams/frameworks/codex.py`*

Thin adapter for the OpenAI Codex CLI. Subclasses `AgentsMdAdapter` and reuses its rendering wholesale — Codex has no user-authored persona-file format analogous to `.claude/agents/*.md` or `.github/agents/*.agent.md`, so `AGENTS.md` content is the primary user-facing lever.

- **framework_id:** `'codex'`
- **Output format:** Agent files: `.agents/<slug>.md` (inherited from `AgentsMdAdapter`); Instructions: repo-root `AGENTS.md` — Codex loads global instructions from `~/.codex/AGENTS.md` (or `AGENTS.override.md`), then walks applicable project and nested-directory `AGENTS.md` files toward the working directory
- **Handoffs:** Manifest sidecar (`references/runtime-handoffs.json`), same as `agents-md` (`handoff_delivery_mode()` inherited, returns `'manifest'`)
- **Agents dir:** `<project>/.agents/` (same layout as `agents-md`)

**Current behavior notes:**

- Overrides only `render_instructions_file` (to swap in a Codex-specific generated notice describing the nested-directory walk) and `get_agents_dir` (explicit, though it returns the same `.agents` path `AgentsMdAdapter` would). Every other method — `render_agent_file`, `get_file_extension`, `required_front_matter_keys`, `supports_handoffs`, `handoff_delivery_mode`, `finalize_output_path` — is inherited from `AgentsMdAdapter` unmodified.
- MCP servers are configured separately in `.codex/config.toml`, emitted by [`codex_mcp_emit`](codex-mcp-emit.md) when the `codex:mcp` host-feature token is enabled and the project declares `mcp_servers[]`.
- Nested-directory `AGENTS.md` placement (subdirectory-scoped refinements) is documented but not yet built.

---

## Runtime Handoff Artifact Contract

Adapters using manifest-based handoff delivery (`CopilotCLIAdapter`, `ClaudeAdapter`, `AgentsMdAdapter`, and `CodexAdapter` — which inherits `AgentsMdAdapter`'s `'manifest'` mode) rely on extracted handoff metadata generated from rendered content and emitted as `references/runtime-handoffs.json` when handoffs exist. Native-handoff adapters (`CopilotVSCodeAdapter`, `GooseAdapter`) keep handoff semantics inline (for Goose, encoded directly in the recipes).
