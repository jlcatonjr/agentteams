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
3. `target_framework` (`str`): one of `copilot-vscode`, `copilot-cli`, `claude`, or `goose`. (`agents-md` is generate-only and is not a valid convert target.)
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
2. The instructions file is placed at the adapter's `finalize_output_path` location — `copilot-instructions.md`, `CLAUDE.md`, or, for `goose`, the repo-root `AGENTS.md` (front matter stripped) — and rendered through the target adapter's `render_instructions_file`.
3. Emits any adapter sidecars via `extra_output_files` — e.g. `goose`'s repo-root `.goosehints` integrator.
4. Copies passthrough assets such as `SETUP-REQUIRED.md` and `references/`.

### Goose target (`--framework goose`)

Converting an existing team to Goose emits one recipe per agent under `.goose/recipes/*.yaml`, plus the repo-root `AGENTS.md` + `.goosehints`. The orchestrator's `sub_recipes` delegation is reconstructed from each agent's handoffs:

- **`copilot-vscode` sources** keep handoffs inline in their agent files, so delegation wires fully (identical to a native `--framework goose` generation).
- **`claude` / `copilot-cli` sources** strip handoffs at their own generation (handoffs live in `references/runtime-handoffs.json`), so they currently convert to valid but **flat (un-delegated)** recipes. Recovering that delegation from the sidecar is planned (see the handoff-recovery work).

`convert_team` synthesizes the team roster into `manifest["output_files"]` so the Goose adapter's `_team_slugs` can resolve delegation targets.
