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

### `export_to_cai(source_dir, source_framework=None)`

Exports source team files to CAI payload.

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

1. CAI path is intended for deterministic cross-framework transport.
2. Bundle mode emits routing/instructions manifests for external consumers.
3. Framework wrappers are normalized while preserving semantic markdown payload.
