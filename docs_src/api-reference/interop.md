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

Best-effort framework detection from directory shape and file signatures.

Returns one of:

1. `copilot-vscode`
2. `copilot-cli`
3. `claude`

`detect_framework` does not auto-detect `goose` or `agents-md`; those are never inferred as interop **sources** (pass `--interop-source-framework` only for the three above).

### `export_to_cai(source_dir, source_framework=None)`

Exports source team files to CAI payload. Non-agent subdirectories — `references`, `skills`, and `.agentteams-backups` — are skipped, so reference docs and backup copies are never mistaken for agents.

Returns CAI object with keys including:

1. `schema_version`
2. `source_framework`
3. `instructions_binding`
4. `agents`

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

1. CAI path is intended for deterministic cross-framework transport of the **agent body / instructions payload** — given the same source, the same target files are produced each run. It is not a round-trip-stable identity for metadata: an agent's `name`/title is re-derived from the body's first heading on export, and target filenames (and the instructions filename, e.g. `CLAUDE.md` vs `copilot-instructions.md`) are re-derived per target framework on import, so exporting and re-importing does not necessarily reproduce the original names.
2. Bundle mode emits routing/instructions manifests for external consumers.
3. Framework wrappers are normalized while preserving semantic markdown payload.
4. **`goose` is not a supported interop target.** The CLI refuses `--interop-from … --framework goose`: the CAI representation does not carry the handoff graph Goose needs for `sub_recipes` delegation, so the result would be unwired. Use [`--convert-from … --framework goose`](convert.md) instead (it preserves delegation from the source agent files). Enabling interop-to-Goose, by preserving handoffs through the CAI, is planned.
