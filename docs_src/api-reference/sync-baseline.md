# `sync-baseline` — AgentTeamsModule

Real-content baseline snapshot writer for native↔canonical agent [synchronization](multi-sync.md).

> *Source: `agentteams/sync_baseline.py`*

Follows the [`front_matter_baseline` precedent](front-matter-reconcile.md) from `build-log.json`: store **actual content**
(not a hash) at the moment [canonical](canonical.md) is materialized into or absorbed from a given framework,
so a later run can [classify](sync-classifier.md) *why* something differs rather than just detect *that* it does.

---

## Public Constants

- `BASELINE_SCHEMA_VERSION` (`"1.0"`): the baseline format version.
- `BASELINES_SUBDIR` (`"sync-baselines"`): subdirectory inside `.agentteams/canonical/`
  where baseline files live.

---

## Public Functions

### `baseline_path(canonical_dir, framework) -> Path`

Return the path to a framework's baseline file:
`canonical_dir/sync-baselines/<framework>.json`.

### `write_baseline(canonical_dir, framework, native_cai, *, native_source_dir="") -> Path`

Write a real-content baseline snapshot for a framework. Captures agent-level content from
the native deployment's CAI dict at the moment of sync. Returns the path to the written file.

### `load_baseline(canonical_dir, framework) -> dict | None`

Load a framework's baseline, or `None` if no baseline exists.

### `delete_baseline(canonical_dir, framework) -> bool`

Delete a framework's baseline file. Returns `True` if deleted, `False` if it didn't exist.

### `has_baseline(canonical_dir, framework) -> bool`

Check whether a baseline exists for a framework.

---

## Storage Format

Each baseline file (`.agentteams/canonical/sync-baselines/<framework>.json`) is a JSON
document containing:

- `schema_version`: `"1.0"`
- `framework`: the native framework name
- `created_at`: ISO-8601 timestamp
- `source_dir`: the native source directory path at capture time
- `agents`: list of agent dicts (same shape as CAI `agents[]` entries, frozen at sync moment)

A canonical directory with no recorded baseline for a framework is treated identically to
`front_matter_merge.py`'s unknown baseline: apply nothing automatically, report only.
