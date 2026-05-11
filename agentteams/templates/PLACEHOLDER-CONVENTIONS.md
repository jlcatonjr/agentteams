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

### Post-Production-Auditor Specific Placeholders

The `domain/post-production-auditor.template.md` archetype requires five additional manual placeholders:

- `{MANUAL:TRIGGER_CONTRACT_VERSION}` — Semantic version number (e.g., "1.0", "2.1") to track when trigger rules change. Used to version the Trigger Contract section and alert users when audit criteria have been updated. Increment when adding/removing trigger conditions or changing severity tiers.

- `{MANUAL:BULK_MUTATION_THRESHOLD}` — Integer record count threshold (e.g., "100", "5000") that unconditionally triggers a post-production audit. Any mutation affecting this many or more records automatically invokes the auditor; smaller mutations may skip audit if they don't match risk-trigger conditions.

- `{MANUAL:SOURCE_OF_TRUTH_SPEC}` — Query specification (SQL statement, API endpoint list, or reference document path) that defines the expected final state after mutation. Used to verify actual state against expected state. Include table/field names, WHERE predicates, and any identity key definitions.

- `{MANUAL:DUPLICATE_CLUSTER_CAP}` — Maximum number of duplicate-key cluster rows to include in the mandatory sample set (e.g., "50", "100"). Clusters larger than this cap are sampled up to the cap and remainder is tracked as "excluded" in audit output.

- `{MANUAL:AUDIT_SLUG}` — Lowercase identifier slug for the current audit run (e.g., "collector-2026-05-10", "mutation-cleanup-phase-2"). Used to namespace audit artifacts in `tmp/by-week/YYYY-Www/{AUDIT_SLUG}/`. Must be unique per run and should include a date or sequence identifier.

## Section Fencing

Templates may demarcate generated sections using fence markers so that `--merge` can update them without touching user-authored content. See [`FENCE-CONVENTIONS.md`](FENCE-CONVENTIONS.md) for the complete specification.

Every template section must be designated either **FENCED** (module-owned, updated on re-generation) or **USER-EDITABLE** (team-owned, never modified by `--merge`). This designation is recorded in a section manifest comment block at the top of each instrumented template.

## Rules

1. Placeholder names are UPPER_SNAKE_CASE
2. Every placeholder must appear in at least one template
3. Auto-resolved placeholders must map to a field in `team-manifest.schema.json`
4. Manual placeholders must have a description in `SETUP-REQUIRED.md` generation logic
5. Nested placeholders are not supported — `{FOO_{BAR}}` is invalid
