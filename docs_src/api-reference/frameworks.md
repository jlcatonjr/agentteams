# `frameworks` — AgentTeamsModule

Per-framework adapter classes that control how rendered agent content is adjusted for a specific target framework.

See also: [`convert`](convert.md) and [`bridge`](bridge.md) drive these adapters for team migration and runtime bridging; the [interoperability guide](../interoperability.md) covers the CAI normalization path.

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

Adjust an output path's extension for this framework. **Default implementation:** when `file_type` is `agent` or `builder`, rewrites the extension to this framework's `get_file_extension(file_type)` if it differs — e.g. this is what turns `CopilotCLIAdapter`'s planned `.agent.md` paths into plain `.md`. Other file types pass through unchanged by default; `ClaudeAdapter`, `AgentsMdAdapter`, `CodexAdapter` (repo-root `AGENTS.md` two levels above `.codex/agents`), and `GooseAdapter` override this further to relocate the `instructions` file to their framework-native root file (`CLAUDE.md`, `AGENTS.md`).

**Args:**

- `rel_path` (`str`) — Relative output path.
- `file_type` (`str`) — Logical file type (`agent`, `builder`, `instructions`, etc.).

**Returns:** `str` — Path with adjusted extension.

#### `render_builder_file(content, manifest)`

Post-process the rendered team-builder meta-agent. **Default implementation: identity** (returns `content` unchanged), so the Copilot adapters emit the builder as a Markdown agent file. The others override it: `GooseAdapter` wraps the builder as a runnable recipe, so it is not a stray `.md` in the agents directory; `CodexAdapter` emits it as a Codex custom-agent TOML; `AgentsMdAdapter` emits it as a plain-Markdown `.agents` detail file through `render_agent_file`; and `ClaudeAdapter` overrides it to map a canonical `tools: [...]` front-matter line to Claude tool names (it appears only under `write_policy`, whose narrowing replaces the builder's Claude grant); a Claude-shaped builder passes through unchanged.

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

Whether this framework has a first-class skill concept. **Default implementation: `False`.** `ClaudeAdapter` overrides it to return `True` (Claude Code's `skills/<slug>/SKILL.md` directories); every other framework emits operational tool docs as reference documents instead. `CodexAdapter` also returns `True` (Codex skills at `.agents/skills/<slug>/SKILL.md`). Skill placement comes from two hooks: `skill_output_rel_path(slug)` (agents-dir-relative emit path; default `../` + `tool_doc_rel_path`) and `skills_dir(agents_dir)` (the skills root; default the `skills` sibling of the agents dir, which Codex overrides to `<root>/.agents/skills`).

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

**Default implementation:** `"native"` when `supports_handoffs()` is `True`, otherwise `"none"`. `ClaudeAdapter`, `CopilotCLIAdapter`, and `AgentsMdAdapter` override this to return `"manifest"` explicitly (`CodexAdapter` overrides it back to `"native"`: its handoffs travel inside each custom agent's `developer_instructions`), since they strip inline handoffs but still preserve extracted routing metadata.

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

- `render_builder_file` maps a canonical builder `tools:` line (written by `write_policy` narrowing) to Claude names, e.g. `Read, Grep, Glob`. Claude Code can't launch a sub-agent whose tool names don't resolve.

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

Adapter for the OpenAI Codex CLI. Emits each agent as a Codex **custom agent**, a TOML file under `.codex/agents/`. Subclasses `AgentsMdAdapter` only for the framework-neutral `AGENTS.md` rendering.

- **framework_id:** `'codex'`
- **Output format:** Agent files: `.codex/agents/<slug>.toml`, with keys `name` (the slug; authoritative over the file name), `description`, `developer_instructions` (a TOML multi-line string), and `sandbox_mode` (`"workspace-write"` when the canonical tools declare `edit`, otherwise `"read-only"`; omitted for a non-canonical or missing tool list). `sandbox_mode` is a declaration, not enforced (see below). No `model`, provider or approval keys. Instructions: repo-root `AGENTS.md`, written only when absent or already Codex-generated.
- **Handoffs:** `native`. Translated into a "Hand off to" list inside a fenced `codex_translation` block in `developer_instructions`, and parsed back by `parse_agent_source`. No sidecar.
- **Agents dir:** `<project>/.codex/agents/` (`normalize_output_path` appends it to a project-root `--output`)

**Current behavior notes:**

- `tools:` is stated as a self-imposed limit, carried verbatim (Codex does not enforce tool grants). Codex ignores a spawned agent's `sandbox_mode` (codex-cli 0.160.1 drops it when it loads the role), so the session's sandbox, or agentteams' launcher when Codex is run through `.codex/confined-run.example.sh`, governs every agent. The key is still emitted as a declaration, labelled not enforced, because the `AR_WRITE_POLICY` audit reads it. **Per-role limits on Codex are instruction-level only**, except under `write_policy: "orchestrator-only"` (see Role gate below).
- An agent whose canonical tools include `read` or `search` but neither `execute` nor `retrieval` (those already run commands) also gets a **Reading on Codex** section. Codex has no separate read tool, so reading goes through the shell. The section allows read-only commands only (`cat`, `head`, `tail`, print-only `sed -n`, `ls`, `rg`, `grep`, `find`, `wc`), allows no shell operator but `|` between them, and names the forms that write or run code: `sed -i`/`-I`/`-f`/`w`/`e`, `rg --pre`/`--hostname-bin`/`-z`, `find -exec`/`-delete`/`-fprint*`, `tee`, and any other program. It also limits reads to the workspace and names the secret stores never to read (`~/.config/agentteams/`, `~/.ssh/`, `.env`, credential stores). Because Codex does not enforce `sandbox_mode`, the list is the guard.
- An agent whose canonical tools include `execute` or `retrieval` (it already runs commands, so it reads through the shell too) gets a **Secrets on Codex** section with the same secret-store line: read only inside the workspace, and never `~/.config/agentteams/`, `~/.ssh/`, `.env` or credential stores.
- Exactly one title H1: the body's own H1 is kept, and `# {name}` is added only when the body has none.
- `guard_rendered_files` (and the interop import) skip an existing repo-root `AGENTS.md` that was not generated for Codex, with a notice. `AGENTS.override.md` is never emitted.
- An interop import keeps `.github/agents/...` paths as they are, because interop does not copy references. Native generation rewrites them to `.codex/agents/...`.
- Codex loads `.codex/agents/**/*.toml` only, so reference docs under `.codex/agents/references/` are ignored by Codex.
- `codex exec --ephemeral` breaks custom-agent spawning in codex-cli 0.160.1 ("collab spawn failed: ... no rollout found for thread id"): spawned agents need the session's rollout. Run multi-agent teams without `--ephemeral`. Observed in mathAgents' live check, 2026-10-07.
- MCP servers are configured separately in `.codex/config.toml`, emitted by [`codex_mcp_emit`](codex-mcp-emit.md) when the `codex:mcp` host-feature token is enabled and the project declares `mcp_servers[]`.

**Launcher mode (`codex:sandbox`, Phase 1a):**

- A `confined`/`exclusive` `privilege_profile` expands to the `codex:sandbox` host feature. `extra_output_files` then adds `.codex/confined-run.example.sh` on Linux and macOS, after the framework-neutral launcher `sandbox/confine-run.sh` that `super()` emits. The runner is an inert, operator-run example; agentteams never runs it. Before launch it validates `CODEX_HOME` (strictly under `~/.config/agentteams/codex-home/` or outside `~/.config/agentteams`; never `$HOME`, inside the project, or an ancestor of either), creates `config.toml`, `hooks.json`, `AGENTS.md` and the `rules/`, `prompts/`, `skills/` dirs empty if absent, and protects them, so a confined agent can't plant config Codex honours on its next run. An `exclusive` team's `protected_read_paths` become `--exclude` and its `workspace_write_roots` become `--writable`.
- Codex's own sandbox cannot nest inside the launcher, so the runner starts `codex --sandbox danger-full-access` inside `sandbox/confine-run.sh`, and the launcher is the boundary. Writes reach only the project root and `CODEX_HOME`. The agentteams control plane, the prompt roots, `.agentteams/` and `CODEX_HOME`'s `config.toml`/`hooks.json` (when present) are read-only, and credential directories, including `~/.config/agentteams/keys`, are masked.
- `CODEX_HOME` must be writable, so it defaults to a per-project directory outside the repo, `$HOME/.config/agentteams/codex-home/<project>` (override: `AGENTTEAMS_CODEX_HOME`). The runner refuses a `CODEX_HOME` under the key directory, inside the project, or at `$HOME` or above. Authenticate it once outside the launcher (`CODEX_HOME=<dir> codex login`).
- Egress defaults to `host`, because Codex needs its API (`CODEX_CONFINE_EGRESS` overrides it; the launcher's `--egress proxy --proxy ADDR:PORT` gives a loopback proxy instead).
- Every agent command inherits the launcher's write access, `CODEX_HOME` included. The launcher bounds the whole session, not one role. Generation can't verify that Codex is actually launched through the runner.
- **Self-probe.** The runner launches `bash -c "$SELF_PROBE" … codex …` inside the launcher, and the probe `exec`s Codex, so both run under the same profile. Before Codex starts, the probe checks from the inside that the signing-key dir lists empty or is unreadable, that a legacy `*.pem` key is unreadable, that `.agentteams/` can't be written, that `.codex/` and `CODEX_HOME`'s `config.toml`/`hooks.json` can't be written, and, under the role gate, that `.codex/hooks.json` and the gate itself can't be written. Any failure exits 3 without starting Codex. Whether those paths exist is read outside the launcher, because inside, a denied path can look absent.
- `write_policy: "orchestrator-only"` (and `write_policy_frameworks: ["codex"]`) is accepted on codex when `codex:sandbox` is in effect, which takes an explicit `confined`/`exclusive` profile. Its key custody relies on the launcher masking the key directory, so it holds only when Codex runs through the runner. Generation prints that it can't verify the launch.

**Role gate (`write_policy: "orchestrator-only"`, Phase 1b):**

- Codex passes a PreToolUse hook the calling agent's `agent_type` and `agent_id` for every call a spawned agent makes, and neither for the top-level session (probed live on codex-cli 0.160.1). Under the policy, `extra_output_files` also emits `.agentteams/bin/codex-role-gate.py` (pinned package data), the read-only file server Goose uses under the policy (`.agentteams/bin/goose-readfs-mcp.py`), and `.codex/hooks.json`, which runs the gate before every tool call as `"$AGENTTEAMS_PYTHON" -I -S "$AGENTTEAMS_ROOT/…" || exit 2` with a 60-second `timeout`. The gate's files come from `_codex_role_gate_emit` (integrity-pinned).
- Live probes found that every spawned-agent tool reaches PreToolUse tagged with the agent's role: shell, `apply_patch`, web search, spawn and send-message. A generic subagent is tagged `default`. The gate allows the top-level session (no `agent_type`, no `agent_id`) everything. Any call with an `agent_type` or `agent_id` may use only `mcp__agentteams_readfs__{read_file,list_dir,find,grep,stat}`. Any event other than PreToolUse, or a call without `AGENTTEAMS_CODEX_CONFINED=1` (set by the runner), is denied. Codex blocks on exit 2 and lets a call through on exit 1, so every deny, malformed input and internal error exits 2, and `|| exit 2` covers a missing interpreter or file.
- Before launch, the runner refuses to start unless the gate, the server and `.codex/hooks.json` match their pinned sha256 (and aren't symlinks). It resolves an absolute `python3`, or `AGENTTEAMS_CODEX_PYTHON`, outside the launcher, refuses one inside the project or `CODEX_HOME`, and passes it as `AGENTTEAMS_PYTHON`. It also refuses a project `.codex/config.toml` that defines `mcp_servers.agentteams_readfs`. It writes the server's `[mcp_servers.agentteams_readfs]` entry into an empty `CODEX_HOME/config.toml`, or requires it in an operator's own config verbatim, exactly once and as one block. It also passes `--dangerously-bypass-hook-trust`, since Codex otherwise skips untrusted project hooks and the runner keeps `config.toml`, where trust is recorded, read-only. The hook files are read-only inside the launcher (`.codex/` is a prompt root; `.agentteams/` is protected).
- Non-orchestrator agents get a **Reading on Codex (role gate)** section naming those tools instead of the shell allowlist.
- `guard_rendered_files` never overwrites an operator's own `.codex/hooks.json`, meaning a symlink, non-JSON, or any hook that doesn't run the gate. It prints a notice instead, and the runner then refuses to start.
- **Label.** This is harness-level, like Claude's tool grants. It holds while Codex runs the hook, which only the runner arranges, and it relies on Codex's undocumented hook input. Residual: Codex lets a call through when a hook times out (probed live), so the gate is kept fast (stdlib, capped stdin, no other I/O). The OS boundary is still the launcher.
- **Derived outputs.** A checking command's generated outputs (for example `lean/.lake`, gate reports) are writable whenever the session, or the launcher's workspace root, is writable, and blocked for every agent in a read-only session. Codex 0.160 has no per-agent write grant, so these outputs can't be opened to one role only.
- Nested-directory `AGENTS.md` placement (subdirectory-scoped refinements) is documented but not yet built.
