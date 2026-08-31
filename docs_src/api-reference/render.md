# `render` — AgentTeamsModule

Render agent files from templates by resolving placeholders.

Takes a team manifest (from [`analyze.py`](analyze.md)) and a template directory, and produces a list of `(output_path, rendered_content)` pairs ready for the emit phase.

The returned content is framework-agnostic, with one exception: for a component whose manifest entry sets `tools`, `_build_placeholder_map_for_file` → `_component_placeholder_map` resolves the `COMPONENT_TOOLS` placeholder via `_adapter_for_framework(framework)`, so that one placeholder's formatting is already framework-shaped in the rendered output. Otherwise, to produce the final emitted file format, pass each rendered body through the target framework adapter's [`render_agent_file()`](frameworks.md) method before calling [`emit.emit_all()`](emit.md).

> *Source: `agentteams/render.py`*

---

## Functions

### `render_all(manifest, *, templates_dir)`

> *Source: `agentteams/render.py`*

Render all output files described in the manifest.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.
- `templates_dir` (`Path`, keyword-only) — Root directory of the `templates/` folder.

**Returns:** `list[tuple[str, str]]` — List of `(output_path_relative_to_agents_dir, rendered_content)` tuples.

---

### `compute_template_hashes(manifest, *, templates_dir)`

> *Source: `agentteams/render.py`*

Compute SHA-256 hashes of all templates used by the manifest.

**Args:**

- `manifest` (`dict[str, Any]`) — Team manifest from `analyze.build_manifest()`.
- `templates_dir` (`Path`, keyword-only) — Root directory of the `templates/` folder.

**Returns:** `dict[str, str]` — Mapping of `template_relative_path → sha256_hex_digest`, truncated to the first 16 hex characters (`hashlib.sha256(...).hexdigest()[:16]`) — not a full 64-character digest.

---

### `resolve_placeholders(template_text, placeholder_map)`

> *Source: `agentteams/render.py`*

Replace all `{PLACEHOLDER}` tokens in `template_text` using `placeholder_map`.

**Args:**

- `template_text` (`str`) — Raw template content.
- `placeholder_map` (`dict[str, str]`) — Mapping of placeholder name → resolved value.

**Returns:** `str` — Template text with all auto-resolved placeholders substituted.

**Behavior Notes:**

- YAML-front-matter-aware: detects the leading `---`-delimited front-matter range in `template_text`, and for a placeholder value substituted inside that range, auto-quotes/escapes it as a YAML scalar (`_escape_yaml_scalar`) — or, if the substitution point already sits inside an open `"..."` string (`_inside_yaml_quoted_string`), escapes only the inner backslashes/quotes instead of adding another layer of quoting. This keeps a resolved value containing YAML-significant characters (colons, quotes, brackets, etc.) from breaking the front matter it's substituted into.

---

### `collect_unresolved_manual(rendered_text)`

> *Source: `agentteams/render.py`*

Return a list of all unresolved `{MANUAL:*}` token names remaining in rendered text.

**Args:**

- `rendered_text` (`str`) — Already-rendered template content.

**Returns:** `list[str]` — List of full unresolved placeholder tokens, including the `{MANUAL:...}` wrapper (e.g., `['{MANUAL:REFERENCE_DB_PATH}', '{MANUAL:STYLE_REFERENCE_PATH}']`) — not bare placeholder names.

---

### `validate_cross_refs(rendered_files)`

> *Source: `agentteams/render.py`*

Validate that every agent slug referenced in `agents:` YAML blocks resolves to a file in the rendered set.

**Args:**

- `rendered_files` (`list[tuple[str, str]]`) — Output of `render_all()`: list of `(relative_path, content)` pairs.

**Returns:** `list[str]` — List of unresolvable cross-reference error strings. Empty list means all references resolve.

**Validation Behavior Notes:**

- Validation is warning-oriented: unresolved references are returned as warning strings rather than raising.
- The validator suppresses references in explicitly conditional/optional prose, including patterns such as:
	- guarded workflow markers (`If @... in team`)
	- routing-table style rows
	- optional applicability guards (`Applies only when ... is present in team`)
- References inside fenced code blocks are ignored.
- Duplicate unresolved `(file, slug)` pairs are de-duplicated.
- `@orchestrator` is treated as a universally valid routing target even if no corresponding generated file is present in the current rendered set.
- `#file:<path>` companion-reference tokens are validated too, independent of the `@slug` check above and its own distinct warning string: a `#file:references/<name>` token whose target isn't in the rendered file set produces `"<output_path>: #file:<target> does not resolve to a generated reference file"`. Only targets containing `references/` are checked this way — a `#file:` token may also point at a source file, which isn't part of the generated set and can't be validated here. This check runs even on lines the `@slug` conditional-prose patterns above would otherwise skip, and duplicate `(file, #file: target)` pairs are de-duplicated the same way.

---

## Rendering and Validation Contracts

- `render_all()` produces framework-agnostic content except the `COMPONENT_TOOLS` placeholder (see above); framework adapters apply final shaping.
- Placeholder replacement is deterministic for supplied manifest data.
- Manual placeholder collection reports unresolved `{MANUAL:*}` tokens post-render.
- Cross-reference validation is designed to reduce false positives for optional agents while preserving true unresolved-reference signals.

---

## See also

- [Section fencing guide](../section-fencing-guide.md) — how the emit phase fences the rendered regions for later merges.
