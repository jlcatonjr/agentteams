# `bridge` — AgentTeamsModule

Lightweight cross-framework compatibility bridges that preserve source canonical documentation.

> Source: `agentteams/bridge.py`

---

## Public Types

### `BridgeResult`

Summary of bridge generation or bridge check run.

Fields:

1. `written`: written (or would-be-written) paths
2. `skipped`: skipped existing paths
3. `errors`: error messages
4. `dry_run`: whether writes were simulated
5. `check_only`: whether run was check mode
6. `check_ok`: bridge freshness verdict
7. `check_report_path`: report path when check mode runs

Property:

- `success`: `True` when no errors and check passes (for check mode)

---

## Public Function

### `run_bridge(*, source_dir, target_framework, output_root, source_framework=None, dry_run=False, overwrite=False, check_only=False)`

Generate bridge artifacts or validate bridge freshness.

Args:

1. `source_dir` (`Path`): canonical source agent directory.
2. `target_framework` (`str`): target runtime framework.
3. `output_root` (`Path`): root where bridge artifacts are written.
4. `source_framework` (`str | None`): optional source override.
5. `dry_run` (`bool`): simulate writes.
6. `overwrite` (`bool`): overwrite existing bridge artifacts.
7. `check_only` (`bool`): run freshness check only.

Returns:

- `BridgeResult`

Raises:

1. `FileNotFoundError` when source directory is missing.
2. `ValueError` for unknown framework ids.

---

## Artifacts

Bridge artifacts are generated in:

- `references/bridges/<source>-to-<target>/`

Core files:

1. `bridge-manifest.json`
2. `agent-inventory.md`
3. `quickstart-snippet.md`
4. `entrypoint.md`

Check mode also emits:

- `bridge-check.report.md`

---

## Notes

1. Designed for runtime interoperability without full cross-framework regeneration.
2. Hash-based freshness checks detect changed, missing, and newly added source files.
3. Target entry files are emitted to help route users through source orchestrator-first flow.
