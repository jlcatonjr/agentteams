# `agentteams.cli` — CLI Package Decomposition

> *Source: `agentteams/cli/`*

**This page documents the module layout, not the command-line surface.** For flags,
option combinations and their interactions, see
[CLI Reference](../cli-reference.md) — restating them here would duplicate rather than
reference (CH-14).

## Why the package exists

`build_team.py` was a single module holding argument parsing, the generate pipeline,
artifact writers and the security gate. CH-07 caps a module at 1000 lines, so it was
carved into this package. `build_team.py` **re-exports** what it moved, so
`build_team.main`, `build_team._write_memory_index` and friends resolve unchanged — the
carve was behaviour-preserving by construction, and the re-exports are what let the
existing test suite pin that.

The `LENGTH_ALLOWLIST` in `tests/test_code_hygiene.py` is now **empty** — every module in
this package is under the 1000-line CH-07 ceiling on its own merits. `app.py` came down from
1174 to 465 when the pipeline moved out, and `generate.py` from 979 to 974 when the
standalone modes moved to `standalone_modes.py`.

Line counts are deliberately not restated per module here: they change on every carve, and a
doc that hard-codes them goes stale the way this paragraph did. Run
`wc -l agentteams/cli/*.py` for current figures.

## Module map

| Module | Owns |
|---|---|
| `app.py` | Entry point. Dispatches on parsed arguments to the right runner; holds no pipeline logic itself. |
| `parser.py` | Argument parser definition — every flag lives here. |
| `parser_validate.py` | Option-combination validation, carved from `parser.py`. Rejects mutually exclusive pairs (e.g. two bridge modes) *before* any work begins. |
| `generate.py` | The generate / update / check pipeline: analyse → render → merge → emit → attest. |
| `render_pipeline.py` | Template rendering and content-merge helpers used by the pipeline. |
| `artifacts.py` | Writers for the generator-owned artifacts: delivery receipt, eval suite, model routing, memory index, code index. Also owns the memory index's source-scope rules. |
| `commands.py` | Sub-command runners for `--convert`, `--interop-*` and `--bridge-*`. |
| `security_gate.py` | The destructive-action gate: requires a recorded PASS decision, or an explicit waiver, before a destructive operation proceeds. |
| `decision_log.py` | Authenticates rows in the security-decisions log (HMAC signing/verification, chain-intactness checks); carved from `security_gate.py` at its own CH-07 ceiling. |
| `schema_cache.py` | Shared JSON-Schema validation plus a content-hash cache, so re-validating unchanged bytes is free. |
| `goose_switch.py` | Glue for `--goose-source` / `--goose-model` / `--goose-show`. |
| `backup_switch.py` | Glue for `--stale-check` / `--stale-remediate` / `--prune-backups` / `--backup-mirror`. |
| `fleet_switch.py` | Glue for `--fleet` / `--fleet-frameworks` / `--fleet-report`. |
| `package_switch.py` | Glue + dispatch for `--package-team` / `--package-source-framework`. |
| `sync_switch.py` | Glue + dispatch for `--sync-init` / `--sync` / `--sync-since` / `--pin`. |
| `recipe_check.py` | Standalone structural validator for Goose recipe YAML. |
| `standalone_modes.py` | The "do one thing and exit" modes carved out of `generate.py`: restore-backup, scan-security, check-budget, template pinning, and the retrieval utilities. They were never part of the generate pipeline. |
| `output_target.py` | Resolves and validates the output directory, including the foreign-output refusal behind `--allow-foreign-output`. |
| `post_emit_checks.py` | Checks that run after the write phase and cannot change its outcome. |
| `code_index_artifacts.py` | Writers for the code & API index cache (`references/code-index/`, gitignored). |
| `json_mode.py` | `--json` output shaping. |
| `exit_codes.py` | The named exit-code constants, so a status is set in one place and read everywhere. |

## Two behaviours worth knowing when reading this package

**The memory index's scope lives in `artifacts.py`, not `memory_index.py`.**
`_memory_index_sources` decides *what* to index and `_memory_index_root` decides what
relative paths are relative *to*; `memory_index.py` only builds an index over whatever
it is handed. `_SCRATCH_DIR_NAMES` / `_is_durable_source` exclude backup and cache
directories from every recursive scan — without that filter the index reached 2120
documents, 1488 of them backup snapshots, in a 51 MB committed artifact.

**Reporting never changes an outcome.** `generate.py` calls
[`update_report.report_run`](update-report.md) after the write phase and only when
`--dry-run` was not passed. A failure to write the report must not turn a successful
update into a failed one.

## Related pages

- [CLI Reference](../cli-reference.md) — flags and option semantics
- [`update_report`](update-report.md) — the `update.report.md` record
- [`memory_index`](memory-index.md) — index construction and path storage
- [`output_plan`](output-plan.md) — which files a manifest produces
