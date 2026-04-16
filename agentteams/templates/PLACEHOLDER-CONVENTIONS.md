# Placeholder Conventions

All templates in this directory use two placeholder syntaxes:

## Auto-Resolved Placeholders

```
{PLACEHOLDER_NAME}
```

These are filled automatically by the rendering engine from the project description and analysis manifest. Examples:

- `{PROJECT_NAME}` — The project name
- `{PROJECT_GOAL}` — One-sentence project goal
- `{PRIMARY_OUTPUT_DIR}` — Path to the primary output directory
- `{AGENT_SLUG_LIST}` — Comma-separated list of all agent slugs
- `{DOMAIN_AGENT_SLUGS}` — Comma-separated list of domain agent slugs
- `{WORKSTREAM_EXPERT_SLUGS}` — Comma-separated list of workstream expert slugs
- `{AUTHORITY_HIERARCHY}` — Formatted authority hierarchy
- `{DELIVERABLE_TYPE}` — Primary deliverable type (e.g., "code", "documents", "data")
- `{STYLE_REFERENCE_PATH}` — Path to the style guide or voice samples file (null if not provided)
- `{DIAGRAM_TOOLS}` — Diagram tool(s) detected from the project description (e.g., "Mermaid or Graphviz/DOT")
- `{DIAGRAM_EXTENSION}` — Default file extension for diagram source files (e.g., "mmd")
- `{COMPONENT_SLUG}` — Generic `<component-slug>` pattern used in file naming conventions
- `{TOOL_DOCS_URL}` — Official documentation URL for the tool (from brief.json `tools[].docs_url`)
- `{TOOL_API_SURFACE}` — Key classes, functions, and APIs the agent must understand (from brief.json `tools[].api_surface`)
- `{TOOL_COMMON_PATTERNS}` — Common usage patterns, anti-patterns, and version-specific gotchas (from brief.json `tools[].common_patterns`)
- `{UNRESOLVED_TOOL_LIST}` — Markdown bullet list of tools missing `docs_url`, `api_surface`, or `common_patterns` after auto-enrichment (used in `tool-doc-researcher.template.md`)

## Manual-Required Placeholders

```
{MANUAL:PLACEHOLDER_NAME}
```

These cannot be inferred from the project description and require human completion. They are collected into `SETUP-REQUIRED.md` when the team is generated. Examples:

- `{MANUAL:SPECIFIC_FILE_PATHS}` — Exact paths within the project codebase
- `{MANUAL:API_ENDPOINT_NAMES}` — Proprietary API endpoints
- `{MANUAL:STYLE_EXEMPLARS}` — Specific style example documents
- `{MANUAL:EXTERNAL_REPO_PATHS}` — Exact paths to external read-only repositories
- `{MANUAL:TOOL_DOCS_URL}` — Documentation URL when not provided in `tools[].docs_url`
- `{MANUAL:TOOL_API_SURFACE}` — Key API surface when not provided in `tools[].api_surface`
- `{MANUAL:TOOL_COMMON_PATTERNS}` — Common patterns when not provided in `tools[].common_patterns`

## Section Fencing

Templates may demarcate generated sections using fence markers so that `--merge` can update them without touching user-authored content. See [`FENCE-CONVENTIONS.md`](FENCE-CONVENTIONS.md) for the complete specification.

Every template section must be designated either **FENCED** (module-owned, updated on re-generation) or **USER-EDITABLE** (team-owned, never modified by `--merge`). This designation is recorded in a section manifest comment block at the top of each instrumented template.

## Rules

1. Placeholder names are UPPER_SNAKE_CASE
2. Every placeholder must appear in at least one template
3. Auto-resolved placeholders must map to a field in `team-manifest.schema.json`
4. Manual placeholders must have a description in `SETUP-REQUIRED.md` generation logic
5. Nested placeholders are not supported — `{FOO_{BAR}}` is invalid
