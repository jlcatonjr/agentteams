# `ingest` — AgentTeamsModule

Parse project descriptions into a normalized dict.

Accepts JSON files matching `project-description.schema.json`, Markdown briefs with section headings, and plain Markdown fallback (unstructured). When `existing_project_path` is set, scans the directory tree to supplement missing fields.

> *Source: `src/ingest.py`*

---

## Functions

### `load(source, *, scan_project=True)`

> *Source: `src/ingest.py`*

Load and return a normalized project description dict.

**Args:**

- `source` (`str | Path`) — Path to a `.json` or `.md` project description file.
- `scan_project` (`bool`, keyword-only) — If `True` and `existing_project_path` is set in the description, scan the project directory for additional context. Default: `True`.

**Returns:** `dict[str, Any]` — Normalized project description conforming to `schemas/project-description.schema.json`.

**Raises:**

- `FileNotFoundError` — If `source` does not exist.
- `ValueError` — If `source` cannot be parsed or fails validation.

---

### `parse_dependency_manifests(project_path)`

> *Source: `src/ingest.py`*

Parse all dependency manifest files found in a project directory.

Recognizes: `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`.

**Args:**

- `project_path` (`Path`) — Root directory of the project to scan.

**Returns:** `list[dict[str, Any]]` — List of dependency dicts, each with keys `name`, `version`, `category`.

---

### `validate(description)`

> *Source: `src/ingest.py`*

Validate a project description dict and return a list of error strings.

**Args:**

- `description` (`dict[str, Any]`) — Project description dict (typically from `load()`).

**Returns:** `list[str]` — List of validation error messages. Empty list means the description is valid.
