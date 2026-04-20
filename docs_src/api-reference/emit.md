# `emit` — AgentTeamsModule

Write rendered agent files to disk.

Takes the list of `(output_path, content)` pairs from `render.py` and writes them to the target output directory with dry-run support, overwrite protection, and automatic backup before destructive writes.

> *Source: `agentteams/emit.py`*

---

## Classes

### `EmitResult`

> *Source: `agentteams/emit.py`*

Results of an emit operation.

**Attributes:**

- `written` (`list[str]`) — Relative paths of files written successfully.
- `merged` (`list[str]`) — Relative paths of files updated via fenced-section merge.
- `skipped` (`list[str]`) — Relative paths of files skipped (already up to date or user declined overwrite).
- `errors` (`list[str]`) — Error messages for any failed writes.
- `dry_run` (`bool`) — `True` if this result is from a dry-run invocation.

**Properties:**

- `success` (`bool`) — `True` if `errors` is empty.

---

---

### `BackupResult`

> *Source: `agentteams/emit.py`*

Result of a backup operation.

**Attributes:**

- `backup_path` (`Path | None`) — Absolute path to the timestamped backup directory, or `None` if no backup was taken (e.g. output directory did not exist).
- `files_backed_up` (`int`) — Number of files copied into the backup.
- `skipped` (`bool`) — `True` if the backup was suppressed (`--no-backup` or `dry_run=True`).

---

## Functions

### `emit_all(rendered_files, *, output_dir, dry_run=False, overwrite=False, merge=False, yes=False)`

> *Source: `agentteams/emit.py`*

Write rendered files to `output_dir`.

**Args:**

- `rendered_files` (`list[tuple[str, str]]`) — List of `(relative_output_path, content)` from `render_all()`.
- `output_dir` (`Path`, keyword-only) — Absolute path to the agents output directory.
- `dry_run` (`bool`, keyword-only) — If `True`, print actions without writing any files. Default: `False`.
- `overwrite` (`bool`, keyword-only) — If `True`, overwrite existing files without prompting. Default: `False`.
- `merge` (`bool`, keyword-only) — If `True`, update only fenced template regions in existing files, preserving user-authored content. Default: `False`.
- `yes` (`bool`, keyword-only) — If `True`, answer `'yes'` to all interactive prompts. Default: `False`.

**Returns:** `EmitResult` — Results of all write operations.

---

### `print_summary(result, manifest)`

> *Source: `agentteams/emit.py`*

Print a human-readable summary of an emit operation to stdout.

**Args:**

- `result` (`EmitResult`) — Result from `emit_all()`.
- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.

---

---

### `backup_output_dir(output_dir, *, files_to_backup=None, dry_run=False)`

> *Source: `agentteams/emit.py`*

Copy existing agent files to a timestamped backup directory before a write.

The backup is placed at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/`. If `files_to_backup` is given, only those relative paths are backed up; otherwise every file in `output_dir` is copied (excluding the backup directory itself).

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

### `restore_backup(backup_path, output_dir)`

> *Source: `agentteams/emit.py`*

Restore files from a backup directory into `output_dir`, overwriting current content.

**Args:**

- `backup_path` (`Path`) — Absolute path to the timestamped backup directory.
- `output_dir` (`Path`) — Absolute path to the agents output directory to restore into.

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
