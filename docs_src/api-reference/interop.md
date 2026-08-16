# `interop` — AgentTeamsModule

Canonical Agent Interface (CAI)-based interoperability pipeline across supported frameworks.

> Source: `agentteams/interop.py`

---

## Public Types

### `InteropResult`

Summary of an interop run.

Fields:

1. `converted`: target files converted/written.
2. `skipped`: skipped target files.
3. `errors`: pipeline errors.
4. `bundle_files`: emitted bundle artifact files.
5. `dry_run`: whether run was simulated.

Property:

- `success`: `True` when `errors` is empty.

---

## Public Functions

### `detect_framework(source_dir)`

Best-effort framework detection from directory shape and file signatures. Checks run in order; the first match wins:

1. `canonical` — a `team.cai.json` marker file is present at `source_dir` (the durable CAI directory format).
2. `claude` — `.claude` appears among `source_dir`'s path parts.
3. `goose` — `.goose` appears among the path parts (a Goose-native `.goose/recipes` source team).
4. `agents-md` — `.agents` appears among the path parts (an `.agents/<name>.md` source team).
5. `copilot-cli` — both `.github` and `copilot` appear among the path parts.
6. Otherwise, falls back to scanning `*.md` files directly under `source_dir`: `copilot-vscode` if any file's name ends `.agent.md` or its front matter has `user-invocable:`/`handoffs:`; `claude` if any file's front matter has `allowed-tools:` or a bracket-free `tools:` scalar; `copilot-cli` if none of the above match.

Pass `--interop-source-framework` to override detection when the heuristics guess wrong.

### `export_to_cai(source_dir, source_framework=None)`

Exports source team files to CAI payload. Non-agent subdirectories — `references`, `skills`, and `.agentteams-backups` — are skipped, so reference docs and backup copies are never mistaken for agents.

Returns CAI object with keys including:

1. `schema_version`
2. `source_framework`
3. `instructions_binding`
4. `agents`
5. `skills` — captured first-class skills (only for frameworks with a skill concept)
6. `mcp_servers` — framework-neutral MCP server definitions captured from the pipeline's managed artifact
7. `references` — present only when the source team has a non-empty `references/` directory
8. `framework_extensions` — present only when the source framework contributes project-level config (e.g., goose recipe parameters/response/retry)

### `import_from_cai(cai, target_framework, target_dir, *, dry_run=False, overwrite=False)`

Imports CAI payload into target framework files.

Returns:

- `InteropResult`

### `run_interop(source_dir, target_framework, target_dir, *, source_framework=None, mode='direct', dry_run=False, overwrite=False)`

End-to-end interop operation.

Modes:

1. `direct`: target files only
2. `bundle`: target files plus compatibility artifacts

Bundle artifacts are emitted under `references/interop/<source>-to-<target>/`.

---

## Notes

1. CAI path is intended for deterministic cross-framework transport of the **agent body / instructions payload** — given the same source, the same target files are produced each run. It is not a round-trip-stable identity for metadata: an agent's `name`/title is taken from the body's front-matter `name:` key on export, falling back to the body's first heading only when no `name:` is present, and target filenames (and the instructions filename, e.g. `CLAUDE.md` vs `copilot-instructions.md`) are re-derived per target framework on import, so exporting and re-importing does not necessarily reproduce the original names.
2. Bundle mode emits routing/instructions manifests for external consumers.
3. Framework wrappers are normalized while preserving semantic markdown payload.
4. `goose` **is** a supported interop target. The CAI's `agents[].handoffs` and `capabilities.tool_scopes` carry enough for the goose adapter to wire the target natively on import: handoffs render as `sub_recipes` delegation and tool scopes as `recipe_extensions`. Goose's instructions file (`AGENTS.md`) is placed at the project root, two directory levels above `.goose/recipes`, unlike other frameworks which place it beside the agents directory.
