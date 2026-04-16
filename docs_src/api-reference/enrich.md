# `enrich` — AgentTeamsModule

Default-value audit and context-aware enrichment for generated agent teams.

After build, scans agent files for unresolved `{MANUAL:*}` placeholders and underdeveloped template sections, exports an audit CSV, then attempts auto-enrichment using rule-based fills, project source scanning, and a built-in tool metadata catalog. An optional AI pass via the `copilot` CLI can fill anything that rule-based logic cannot resolve.

> *Source: `agentteams/enrich/`*

---

## Classes

### `DefaultFinding`

> *Source: `agentteams/enrich/_models.py`*

A single default-value (underdeveloped template) finding.

**Attributes:**

- `file` (`str`) — Relative path to the agent file.
- `category` (`str`) — `'MANUAL_PLACEHOLDER'`, `'GENERIC_SECTION'`, `'TOOL_METADATA'`, or `'MISSING_TOOL_REF'`.
- `token` (`str`) — The placeholder name or section label, e.g. `COMPONENT_SPEC` or `STYLE_REFERENCE_PATH`.
- `line_no` (`int`) — 1-based line number of the finding in the agent file.
- `section` (`str`) — Nearest `##` section heading above the finding, or `''` if in front matter.
- `context_snippet` (`str`) — 1–2 lines of surrounding context.
- `status` (`str`) — `'pending'`, `'auto_filled'`, or `'ai_filled'` (set by `auto_enrich` or `ai_enrich`).
- `auto_suggestion` (`str | None`) — Suggested replacement value, if one was computed.

---

## Functions

### `scan_defaults(file_map, manifest, project_path=None)`

> *Source: `agentteams/enrich/_audit.py`*

Scan generated agent files for unresolved default template elements.

Detects: `{MANUAL:TOKEN}` placeholders, tool reference files with incomplete metadata, underdeveloped sections (comment-only bodies, generic boilerplate), and packages imported in project source with no reference file.

**Args:**

- `file_map` (`dict[str, str]`) — Rendered file content keyed by relative path.
- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.
- `project_path` (`Path | None`) — Project root; enables import scanning for coverage gaps.

**Returns:** `list[DefaultFinding]`

---

### `auto_enrich(findings, file_map, manifest, project_path=None)`

> *Source: `agentteams/enrich/_enrich.py`*

Apply all resolvable fills to `file_map`, returning an enriched copy.

Applies three fill strategies in order: rule-based fills for known token patterns, notebook-header scanning for component specs, and the built-in tool metadata catalog for `TOOL_DOCS_URL`, `TOOL_API_SURFACE`, and `TOOL_COMMON_PATTERNS` tokens.

**Args:**

- `findings` (`list[DefaultFinding]`) — Findings from `scan_defaults()`.
- `file_map` (`dict[str, str]`) — Rendered file content keyed by relative path.
- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.
- `project_path` (`Path | None`) — Absolute path to the project repo root (enables notebook scanning).

**Returns:** `tuple[dict[str, str], list[DefaultFinding]]` — `(enriched_file_map, updated_findings)`

---

### `ai_enrich(findings, file_map, manifest, *, copilot_exe)`

> *Source: `agentteams/enrich/_enrich.py`*

Fill remaining pending findings using the `copilot` CLI.

Called after `auto_enrich` to handle tokens that rule-based logic could not resolve. Invokes the standalone `copilot` CLI in non-interactive mode with the project context and list of pending findings.

**Args:**

- `findings` (`list[DefaultFinding]`) — Findings list (pending items will be targeted).
- `file_map` (`dict[str, str]`) — Current rendered file map (will be updated in place).
- `manifest` (`dict[str, Any]`) — Team manifest.
- `copilot_exe` (`str`, keyword-only) — Absolute path to the `copilot` executable (from `shutil.which`).

**Returns:** `tuple[dict[str, str], list[DefaultFinding]]` — `(enriched_file_map, updated_findings)`

---

### `generate_setup_required(findings, manifest)`

> *Source: `agentteams/enrich/_enrich.py`*

Generate `SETUP-REQUIRED.md` listing only genuinely pending findings.

Called after `auto_enrich` so that already-resolved tokens are excluded.

**Args:**

- `findings` (`list[DefaultFinding]`) — Findings list with updated statuses after `auto_enrich`.
- `manifest` (`dict[str, Any]`) — Team manifest.

**Returns:** `str` — Markdown string suitable for writing to `SETUP-REQUIRED.md`.

---

### `export_csv(findings, csv_path)`

> *Source: `agentteams/enrich/_enrich.py`*

Write findings to a CSV file.

**Args:**

- `findings` (`list[DefaultFinding]`) — Findings to export.
- `csv_path` (`Path`) — Destination path for the CSV file.

---

### `load_csv(csv_path)`

> *Source: `agentteams/enrich/_enrich.py`*

Load findings from a previously exported CSV file.

**Args:**

- `csv_path` (`Path`) — Path to a CSV file written by `export_csv()`.

**Returns:** `list[DefaultFinding]`

---

### `print_enrich_summary(findings, *, verbose=False)`

> *Source: `agentteams/enrich/_enrich.py`*

Print a human-readable enrichment summary to stdout.

**Args:**

- `findings` (`list[DefaultFinding]`) — Findings list after `auto_enrich` has set statuses.
- `verbose` (`bool`, keyword-only) — If `True`, list all pending findings individually. Default: `False`.

---

### `scan_project_imports(project_path)`

> *Source: `agentteams/enrich/_tools.py`*

Scan Python source files in `project_path` for import statements and return a mapping of imported package names to their canonical PyPI names.

**Args:**

- `project_path` (`Path`) — Root directory to scan (walks recursively).

**Returns:** `dict[str, str]` — `{import_name: pypi_package_name}`

---

### `build_tool_catalog(packages, *, fetch_pypi=True)`

> *Source: `agentteams/enrich/_tools.py`*

Build a metadata catalog for a list of packages, combining the built-in static catalog with optional live PyPI lookups.

**Args:**

- `packages` (`list[str]`) — PyPI package names to look up.
- `fetch_pypi` (`bool`, keyword-only) — If `True`, fetch live metadata from PyPI for packages not in the static catalog. Default: `True`.

**Returns:** `dict[str, dict[str, str]]` — Keyed by normalized package name; values contain `docs_url`, `api_surface`, and `common_patterns`.
