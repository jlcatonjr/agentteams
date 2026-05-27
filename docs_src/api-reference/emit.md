# `emit` — AgentTeamsModule

Write rendered agent files to disk.

Takes the list of `(output_path, content)` pairs from `render.py` and writes them to the target output directory with dry-run support and overwrite protections.

Backup behavior is provided by `backup_output_dir()` and by CLI orchestration flows that call it before destructive writes; `emit_all()` does not automatically trigger backup on its own.

> *Source: `agentteams/emit.py`*

---

## Classes

### `DryRunReport`

> *Source: `agentteams/emit.py`*

Structured preview of what an emit/update would write without performing the write.

Only present when `emit_all(..., dry_run=True)`. Serves as an extension point for Plan 3 shrink-delta notices.

**Attributes:**

- `entries` (`list[DryRunEntry]`) — List of planned write actions, each with:
  - `action` (`str`) — One of `'write'`, `'merge'`, `'skip'`, `'error'`
  - `fence_actions` (`list[dict[str, Any]]`) — Details of fenced-section merge actions (if `action == 'merge'`)
  - `delta_bytes` (`int`) — Estimated byte delta for the operation

- `notices` (`list[str]`) — Human-readable notices for Plan 3 extension (e.g., shrink alerts). Empty by default.

---

### `DryRunEntry`

> *Source: `agentteams/emit.py`*

One per-file row in the dry-run preview. Populated by `emit_all(..., dry_run=True)` into `DryRunReport.entries`.

**Attributes:**

- `path` (`str`) — Absolute path of the file the action would touch.
- `action` (`str`) — One of `WRITE`, `OVERWRITE`, `MERGE`, `MERGE-OVERWRITE-FENCED`, `UNCHANGED`, `SKIP`.
- `fence_actions` (`list[dict[str, Any]]`) — Per-fence merge details for `MERGE` / `MERGE-OVERWRITE-FENCED` rows (each dict carries `fence_id` and `action`). Empty for other actions.
- `delta_bytes` (`int`) — Estimated byte delta for the action.

---

### `EmitResult`

> *Source: `agentteams/emit.py`*

Results of an emit operation.

**Attributes:**

- `written` (`list[str]`) — Relative paths of files written successfully.
- `merged` (`list[str]`) — Relative paths of files updated via fenced-section merge.
- `unchanged` (`list[str]`) — Relative paths of files whose on-disk content was already identical to the rendered output (no write performed). **Note:** Files in this list were not written (byte-equality check); callers should not count them in result-counting logic.
- `skipped` (`list[str]`) — Relative paths of files skipped (already up to date or user declined overwrite).
- `errors` (`list[str]`) — Error messages for any failed writes.
- `dry_run` (`bool`) — `True` if this result is from a dry-run invocation.
- `dry_run_report` (`DryRunReport | None`) — Structured dry-run preview (only when `dry_run=True`).
- `notices` (`list[str]`) — Aggregated notices from all operations (Plan 3 extension point). May include shrink alerts, deprecation warnings, etc.
- `shrink_blocked` (`list[str]`) — *(T2.D5)* Absolute paths whose merge was skipped because `shrink_policy="halt"` detected a destructive shrink. Distinct from `skipped` (overwrite declined) and `errors` (true failures) — these are intentional non-writes the operator can review.

**Properties:**

- `success` (`bool`) — `True` if `errors` is empty.

---

### `MergeResult`

> *Source: `agentteams/emit.py`*

Result for a single fenced-content merge operation.

> **Boundary note:** `MergeResult` is part of the documented API surface for merge diagnostics. Most callers should still use `emit_all()` and rely on `EmitResult` for operation-level outcomes.

**Attributes:**

- `sections_replaced` (`list[str]`) — Section IDs whose content was replaced from the newly rendered file.
- `sections_added` (`list[str]`) — Section IDs present in the new render but absent in the existing file.
- `sections_orphaned` (`list[str]`) — Section IDs present in existing file but absent in new render.
- `parse_errors` (`list[str]`) — Parse-related error messages from fenced-region extraction/validation.
- `unchanged` (`list[str]`) — Section IDs that were identical in both files (no write needed).
- `merged_content` (`str`) — Final merged file content. Empty string when parse fails.
- `shrink_notices` (`list[str]`) — Per-section human-readable notices (Plan 3) when a regenerated fence body is materially shorter or less specific than the existing on-disk version. Used for alerting on potential loss of detail during merge.
- `lost_fence_bodies` (`dict[str, str]`) — W22 data-loss recovery: full pre-merge body of every fence that fired a shrink notice, keyed by `section_id`. Persisted as a `<rel_path>.lost.<sid>.md` sidecar inside the backup dir by `emit_all` when `backup_path` is provided. Empty when no shrink fired.

**Properties:**

- `has_errors` (`bool`) — `True` when parse errors are present.
- `content_changed` (`bool`) — `True` when at least one section was replaced or added.

---
---

### `BackupResult`

> *Source: `agentteams/emit.py`*

Result of a backup operation.

**Attributes:**

- `backup_path` (`Path | None`) — Absolute path to the timestamped backup directory, or `None` if no backup was taken (e.g. output directory did not exist).
- `files_backed_up` (`int`) — Number of files copied into the backup.
- `extra_files_removed` (`int`) — Number of output files removed during restore when `remove_extra=True`.
- `skipped` (`bool`) — `True` if the backup was suppressed (`--no-backup` or `dry_run=True`).

---

## Functions

### `emit_all(rendered_files, *, output_dir, dry_run=False, overwrite=False, merge=False, yes=False, shrink_policy="warn", backup_path=None)`

> *Source: `agentteams/emit.py`*

Write rendered files to `output_dir`.

**Args:**

- `rendered_files` (`list[tuple[str, str]]`) — List of `(relative_output_path, content)` from `render_all()`.
- `output_dir` (`Path`, keyword-only) — Absolute path to the agents output directory.
- `dry_run` (`bool`, keyword-only) — If `True`, print actions without writing any files. Default: `False`.
- `overwrite` (`bool`, keyword-only) — If `True`, overwrite existing files without prompting. Default: `False`.
- `merge` (`bool`, keyword-only) — If `True`, update only fenced template regions in existing files, preserving user-authored content. Default: `False`.
- `yes` (`bool`, keyword-only) — If `True`, answer `'yes'` to all interactive prompts. Default: `False`.
- `shrink_policy` (`str`, keyword-only) — *(T2.D5)* Behaviour when a fenced-region merge would lose concrete references (paths, identifiers, list items). One of:
    - `"warn"` (default, back-compatible): log the shrink notice into `EmitResult.notices` and proceed with the smaller content.
    - `"halt"`: log the notice, refuse the write, and append the path to `EmitResult.shrink_blocked`. Use to enforce strict fence-content preservation for self-team daily runs.
    - `"allow"`: suppress notices and write the smaller content silently. Use for one-time recovery when a previous halt was over-cautious.

    The fence-id allowlist `_LIVE_DATA_FENCES` (`threat_intelligence`, `threat_data`) is exempt from the shrink heuristic — those fences are filled each run from live CISA KEV / NVD / OSV feeds, and CVE rotation is expected behavior, not user-content deletion. The canonical history for these fences is the cache JSON (`references/security-vulnerability-watch.json`), not the embedded snapshot.

- `backup_path` (`Path | None`, keyword-only) — When provided and a shrink notice fires under `warn`, the full pre-merge body of every shrunken fence is written to `<backup_path>/<rel_path>.lost.<sid>.md` and the corresponding `EmitResult.notices` entry is annotated with `— recovery: <sidecar-path>`. This makes `warn` recoverable even when the operator didn't catch the notice — the sidecar is the durable evidence of what was dropped. Default: `None` (no sidecar written; notices are not annotated).

**Returns:** `EmitResult` — Results of all write operations.

**Raises:**

- `ValueError` — If both `overwrite` and `merge` are `True` (mutually exclusive).

---

### `print_summary(result, manifest)`

> *Source: `agentteams/emit.py`*

Print a human-readable summary of an emit operation to stdout.

**Args:**

- `result` (`EmitResult`) — Result from `emit_all()`.
- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.

---
---

### `print_dry_run_report(result, manifest, *, fmt='text')`

> *Source: `agentteams/emit.py`*

Print the structured dry-run plan recorded on `EmitResult.dry_run_report`.

**Args:**

- `result` (`EmitResult`) — Result returned by `emit_all(..., dry_run=True)`. If `result.dry_run_report` is `None`, the function is a no-op that prints a one-line note.
- `manifest` (`dict`) — Manifest from `analyze.build_manifest()`; used for header context.
- `fmt` (`str`, keyword-only) — `'text'` prints a per-file action table plus aggregated counts and notices; `'json'` prints a single JSON document to stdout suitable for `jq` piping. Default: `'text'`.

**Returns:** `None`.

---

### `backup_output_dir(output_dir, *, files_to_backup=None, dry_run=False)`

> *Source: `agentteams/emit.py`*

Copy existing agent files to a timestamped backup directory before a write.

The backup is placed at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/`. If `files_to_backup` is given, only those relative paths are backed up (plus liaison/security CSV logs when present). If `files_to_backup` is `None`, every file in `output_dir` is copied except backup storage and `references/build-log.json`.

**Args:**

- `output_dir` (`Path`) — Absolute path to the agents output directory.
- `files_to_backup` (`list[str] | None`, keyword-only) — Relative paths to selectively back up. Pass `None` to back up everything. Default: `None`.
- `dry_run` (`bool`, keyword-only) — If `True`, report what would be backed up without writing. Default: `False`.

**Returns:** `BackupResult` — Description of what was backed up.

---

### `list_backups(output_dir)`

> *Source: `agentteams/emit.py`*

Return all available backups for `output_dir`, newest first.

**Args:**

- `output_dir` (`Path`) — Absolute path to the agents output directory.

**Returns:** `list[tuple[str, Path, int]]` — List of `(timestamp_str, backup_path, file_count)` tuples sorted newest-first. Empty list if no backups exist.

---

### `restore_backup(backup_path, output_dir, *, remove_extra=False)`

> *Source: `agentteams/emit.py`*

Restore files from a backup directory into `output_dir`, overwriting current content.

**Args:**

- `backup_path` (`Path`) — Absolute path to the timestamped backup directory.
- `output_dir` (`Path`) — Absolute path to the agents output directory to restore into.
- `remove_extra` (`bool`, keyword-only) — If `True`, remove files in `output_dir` that are not present in the selected backup. Default: `False`.

**Returns:** `int` — Number of files restored.

**Raises:** `FileNotFoundError` — If `backup_path` does not exist.

---

### `file_hash(path)`

> *Source: `agentteams/emit.py`*

Return the SHA-256 hex digest of a file's contents.

> **Note:** This function is public for use in build tooling and tests. It is a utility symbol rather than a core pipeline interface; callers should not rely on it remaining in `emit` across major versions.

**Args:**

- `path` (`Path`) — Path to the file to hash.

**Returns:** `str` — First 8 characters of the SHA-256 hex digest of the file's contents (used for change-detection comparisons).
