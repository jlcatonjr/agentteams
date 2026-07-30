# `memory_index_incremental` — Incremental Index Updates

> *Source: `agentteams/memory_index_incremental.py`*

Patches `references/memory-index.json` in place when exactly one indexed document
changed, instead of rebuilding the whole index. Opt-in via
`AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED=1`.

**Deliberately conservative: it declines far more often than it applies.** Every
decline falls back to a full rebuild, which is always correct, so the failure direction
is slow rather than wrong.

## API

### `try_incremental_sed_update(*, index_path, index, sources, project_name, framework, validate_index, root=None)`

Attempt the patch; return a non-applied result on any risk.

**Args (all keyword-only):**

- `index_path` (`Path`) — The on-disk index to patch.
- `index` (`dict[str, Any]`) — Its parsed contents.
- `sources` (`Iterable[Path | str]`) — Current source files.
- `project_name` (`str`), `framework` (`str`) — Metadata refreshed in the patched index.
- `validate_index` (`callable`) — Schema validator applied to the patched result before
  it is accepted.
- `root` (`Path | None`) — Project root. **Must match what `build_memory_index` was
  given.** The matcher compares stored paths against source paths, so keying by
  absolute path while the index stores relative ones makes every set comparison
  differ — the incremental path would then silently never apply again. Correct output,
  optimisation quietly dead.

**Returns:** `IncrementalUpdateResult` — `.applied` and `.reason`.

## Decline reasons

| `reason` | Meaning |
|---|---|
| `missing_index_file` | Nothing to patch |
| `invalid_index_shape` | `documents`/`postings` are not the expected types |
| `source_set_changed` | The set of indexed paths differs from the current sources |
| `missing_doc_entry` | A source has no matching document |
| `eligible_only_single_changed_doc` | Zero or more than one document changed |
| `invalid_doc_id` | The document's `doc_id` is not an int |

## Migrating an index that stores absolute paths

An index built before `root=` existed stores absolute paths. With `root` now supplied,
the stored set and the offered set differ, so the matcher returns
`source_set_changed` and the run falls back to a full rebuild — **which rewrites the
whole file in the new relative form.** That is the intended migration: applying
incrementally would produce an index mixing absolute and relative paths.

Pinned by `tests/test_memory_index.py::test_legacy_absolute_index_migrates_via_full_rebuild`.

## Why `sed`

The patch is applied by a generated `sed` script over deterministic line ranges, not by
re-serialising JSON — re-serialising a 16 MB index is the cost the incremental path
exists to avoid. `_restore_backup` reverts the file if the patched result fails
validation, so a malformed patch never lands.
