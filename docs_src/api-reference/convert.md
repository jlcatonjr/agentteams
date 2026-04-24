# `convert` — AgentTeamsModule

Format migration utilities for converting existing agent teams between supported frameworks while preserving body prose.

> Source: `agentteams/convert.py`

---

## Public Types

### `ConvertResult`

Summary of a conversion run.

Fields:

1. `converted`: paths written (or that would be written in dry-run)
2. `skipped`: paths skipped due to overwrite policy or classification
3. `errors`: conversion errors
4. `dry_run`: whether writes were simulated

Property:

- `success`: `True` when `errors` is empty

---

## Public Function

### `convert_team(source_dir, target_dir, target_framework, *, project_manifest=None, dry_run=False, overwrite=False)`

Convert an existing team into a target framework format.

Args:

1. `source_dir` (`Path`): source agent directory.
2. `target_dir` (`Path`): destination agent directory.
3. `target_framework` (`str`): one of `copilot-vscode`, `copilot-cli`, `claude`.
4. `project_manifest` (`dict | None`): optional context for adapter rendering.
5. `dry_run` (`bool`): simulate write operations.
6. `overwrite` (`bool`): overwrite existing files when true.

Returns:

- `ConvertResult`

Raises:

1. `ValueError` for unknown framework ids.
2. `FileNotFoundError` when source directory does not exist.

---

## Notes

1. Preserves body prose while rewriting framework wrappers/front matter.
2. Converts instructions file naming between `copilot-instructions.md` and `CLAUDE.md`.
3. Copies passthrough assets such as `SETUP-REQUIRED.md` and `references/`.
