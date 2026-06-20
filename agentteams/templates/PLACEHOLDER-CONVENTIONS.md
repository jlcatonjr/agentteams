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
- `{AGENT_SLUG_LIST}` — Multi-line YAML list of all agent slugs
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

Note: placeholder keys below are legacy-compatible names retained for backward compatibility. Their semantic meaning is domain-agnostic.

- `{MANUAL:TRIGGER_CONTRACT_VERSION}` — Semantic version number (e.g., "1.0", "2.1") to track when trigger rules change. Used to version the Trigger Contract section and alert users when audit criteria have been updated. Increment when adding/removing trigger conditions or changing severity tiers.

- `{MANUAL:BULK_MUTATION_THRESHOLD}` — Integer scope threshold (e.g., "100", "5000") that unconditionally triggers a post-production audit. Any irreversible or high-impact state change affecting this many or more scoped units (records/files/artifacts/endpoints) automatically invokes the auditor; smaller changes may skip audit if they do not match risk-trigger conditions.

- `{MANUAL:SOURCE_OF_TRUTH_SPEC}` — Verification specification (SQL statement, API endpoint list, repository path, or reference document path) that defines expected final state after a claimed completion event. Used to verify actual state against expected state. Include identity definitions and domain predicates.

- `{MANUAL:DUPLICATE_CLUSTER_CAP}` — Maximum number of identity-collision cluster units to include in the mandatory sample set (e.g., "50", "100"). Clusters larger than this cap are sampled up to the cap and remainder is tracked as "excluded" in audit output.

- `{MANUAL:AUDIT_SLUG}` — Lowercase identifier slug for the current audit run (e.g., "collector-2026-05-10", "mutation-cleanup-phase-2"). Used to namespace audit artifacts in `tmp/by-week/YYYY-Www/{AUDIT_SLUG}/`. Must be unique per run and should include a date or sequence identifier.

## Section Fencing

Templates may demarcate generated sections using fence markers so that `--merge` can update them without touching user-authored content. See [`FENCE-CONVENTIONS.md`](FENCE-CONVENTIONS.md) for the complete specification.

### Retrofit fence-id naming (Plan 4 of W21 --update improvements)

When the `agentteams --add-fence-markers PATH` helper retrofits a legacy
(unfenced) file with canonical fence markers, the injected single-region
fence id follows this rule:

- **Default base id:** `content` — matches the fence id `emit._normalize_generated_content` uses for its default whole-body wrap. A subsequent `--update --merge` against a team that *does* emit this file will then replace the fenced body in-place instead of leaving a stale `legacy_body` block and appending a duplicate `content` block (a real bug observed in the 2026-05-20 collector-management cross-repo update).
- **Collision rule:** if `content` is already present in the target file
  (idempotent re-run, or hand-authored fences) the helper picks the smallest
  available `content_<n>` for integer `n >= 1`.
- **Id charset:** must match `^[a-z][a-z0-9_]*$` (same as every other fence id).
- **Migration note:** files retrofitted by an older agentteams (`legacy_body` default) should be re-fenced — sed `s/AGENTTEAMS:BEGIN legacy_body /AGENTTEAMS:BEGIN content /` + matching END — before the next `--update --merge` to avoid the duplication mode.

This convention is enforced by `agentteams/fence_inject.py::_unique_fence_id`
and pinned by `tests/test_fence_inject.py`.

Every template section must be designated either **FENCED** (module-owned, updated on re-generation) or **USER-EDITABLE** (team-owned, never modified by `--merge`). This designation is recorded in a section manifest comment block at the top of each instrumented template.

## Rules

1. Placeholder names are UPPER_SNAKE_CASE
2. Every placeholder must appear in at least one template
3. Auto-resolved placeholders must be emitted by one of the placeholder-map builders — `analyze._build_placeholder_map` (in `agentteams/analyze.py`), `render._component_placeholder_map`/`render._tool_placeholder_map`, `ai_bad_habits.build_catalog_placeholders`, `framework_research.build_framework_placeholders`, or `security_refs.build_security_placeholders`. Note: `team-manifest.schema.json`'s `auto_resolved_placeholders` is a free-form object with **no enumerated properties**, so it does not constrain placeholder names — the builders above are the source of truth.
4. Manual placeholders must have a description in `SETUP-REQUIRED.md` generation logic
5. Nested placeholders are not supported — `{FOO_{BAR}}` is invalid
