# `emit` — AgentTeamsModule

Write rendered agent files to disk.

Takes the list of `(output_path, content)` pairs from `render.py` and writes them to the target output directory with dry-run support and overwrite protection.

> *Source: `agentteams/emit.py`*

---

## Classes

### `EmitResult`

> *Source: `agentteams/emit.py`*

Results of an emit operation.

**Attributes:**

- `written` (`list[str]`) — Relative paths of files written successfully.
- `skipped` (`list[str]`) — Relative paths of files skipped (already up to date or user declined overwrite).
- `errors` (`list[str]`) — Error messages for any failed writes.
- `dry_run` (`bool`) — `True` if this result is from a dry-run invocation.

**Properties:**

- `success` (`bool`) — `True` if `errors` is empty.

---

## Functions

### `emit_all(rendered_files, *, output_dir, dry_run=False, overwrite=False, yes=False)`

> *Source: `agentteams/emit.py`*

Write rendered files to `output_dir`.

**Args:**

- `rendered_files` (`list[tuple[str, str]]`) — List of `(relative_output_path, content)` from `render_all()`.
- `output_dir` (`Path`, keyword-only) — Absolute path to the agents output directory.
- `dry_run` (`bool`, keyword-only) — If `True`, print actions without writing any files. Default: `False`.
- `overwrite` (`bool`, keyword-only) — If `True`, overwrite existing files without prompting. Default: `False`.
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

### `file_hash(path)`

> *Source: `agentteams/emit.py`*

Return the SHA-256 hex digest of a file's contents.

> **Note:** This function is public for use in build tooling and tests. It is a utility symbol rather than a core pipeline interface; callers should not rely on it remaining in `emit` across major versions.

**Args:**

- `path` (`Path`) — Path to the file to hash.

**Returns:** `str` — SHA-256 hex digest string.
