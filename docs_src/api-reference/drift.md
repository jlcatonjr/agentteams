# `drift` — AgentTeamsModule

Detect template-to-instance drift in generated agent teams.

Compares current template hashes against those recorded in `build-log.json` at generation time, and reports which agent files need re-rendering. Drift has two independent dimensions: **content drift** (template text changed) and **structural drift** (team composition changed).

> *Source: `agentteams/drift.py`*

---

## Classes

### `DriftReport`

> *Source: `agentteams/drift.py`*

Results of a content-drift detection run.

**Attributes:**

- `changed_templates` (`list[dict[str, str]]`) — Templates whose hash differs from the build-log.
- `missing_templates` (`list[str]`) — Templates referenced in the build-log that no longer exist.
- `new_templates` (`list[str]`) — Templates found on disk not recorded in the build-log.
- `unchanged` (`list[str]`) — Templates that match the build-log hash.

**Properties:**

- `has_drift` (`bool`) — `True` if any templates have changed since last generation.
- `affected_output_files` (`list[str]`) — Output file paths affected by drifted templates.

---

### `StructuralDiffReport`

> *Source: `agentteams/drift.py`*

Results of a structural diff between a build-log and a current manifest.

**Attributes:**

- `added_files` (`list[dict[str, Any]]`) — Files in the new manifest absent from the old log.
- `removed_files` (`list[dict[str, Any]]`) — Files in the old log absent from the new manifest.
- `drifted_files` (`list[dict[str, Any]]`) — Files present in both but whose template hash changed.
- `unchanged_files` (`list[dict[str, Any]]`) — Files present in both with the same template hash.
- `manifest_changed` (`bool`) — `True` when the new manifest fingerprint differs from the one recorded in the prior build log.
- `team_membership_changed` (`bool`) — `True` when `agent_slug_list` differs between old and new.
- `legacy_log` (`bool`) — `True` when build-log predates schema v1.2 (no `output_files_map`).

**Properties:**

- `has_changes` (`bool`) — `True` if any structural or content changes require action.
- `update_files` (`list[dict[str, Any]]`) — All file entries that need to be written (added + drifted).

---

## Functions

### `load_build_log(agents_dir)`

> *Source: `agentteams/drift.py`*

Load `build-log.json` from an agents directory.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` directory.

**Returns:** `dict[str, Any]` — Parsed build-log.json dict.

**Raises:** `FileNotFoundError` — If `build-log.json` does not exist in `agents_dir`.

---

### `detect_drift(agents_dir, templates_dir, *, build_log=None)`

> *Source: `agentteams/drift.py`*

Detect content drift by comparing current template hashes against the build-log.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` directory containing `build-log.json`.
- `templates_dir` (`Path`) — Path to the templates root directory.
- `build_log` (`dict[str, Any] | None`, keyword-only) — Optional pre-loaded build-log dict. If `None`, `build-log.json` is loaded from `agents_dir`.

**Returns:** `DriftReport`

**Raises:**

- `FileNotFoundError` — If `build-log.json` is not found.
- `ValueError` — If `build-log.json` exists but is malformed JSON.

---

### `print_drift_report(report)`

> *Source: `agentteams/drift.py`*

Print a human-readable drift report to stdout.

**Args:**

- `report` (`DriftReport`) — Result from `detect_drift()`.

---

### `compute_structural_diff(old_log, manifest, templates_dir)`

> *Source: `agentteams/drift.py`*

Compute a structural diff between a stored build-log and a new manifest.

**Args:**

- `old_log` (`dict[str, Any]`) — Previously stored build-log from `load_build_log()`.
- `manifest` (`dict[str, Any]`) — Current team manifest from `analyze.build_manifest()`.
- `templates_dir` (`Path`) — Path to the templates root directory.

**Returns:** `StructuralDiffReport`

---

### `print_structural_diff_report(report)`

> *Source: `agentteams/drift.py`*

Print a human-readable structural diff report to stdout.

**Args:**

- `report` (`StructuralDiffReport`) — Result from `compute_structural_diff()`.

---

### `compute_manifest_fingerprint(manifest)`

> *Source: `agentteams/drift.py`*

Compute a stable hash fingerprint of a team manifest for change detection.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.

**Returns:** `str` — First 16 hex characters of the SHA-256 digest of the canonicalized manifest (stable short fingerprint).

---

### `detect_user_customizations(agents_dir, *, build_log=None)`

> *Source: `agentteams/drift.py`*

Detect generated files whose on-disk hash differs from the hash recorded at generation time.

This is a best-effort advisory pre-write signal used by update workflows. A detected customization does not block execution by itself.

The check reports modified files that still exist on disk; missing files are skipped.

**Args:**

- `agents_dir` (`Path`) — Path to the `.github/agents/` directory.
- `build_log` (`dict[str, Any] | None`, keyword-only) — Optional pre-loaded build-log dict. If `None`, `build-log.json` is loaded from `agents_dir`.

**Returns:** `list[dict[str, str]]` — A list of customization records. Each record includes:

- `path` — Absolute path string for the customized file.
- `rel_path` — Relative path as stored in the build log.
- `reason` — `modified since last build`.

Returns an empty list when the build log is missing, invalid, or has no recorded file hashes.

Read-path OS errors while hashing existing files are not suppressed and may propagate to callers.
