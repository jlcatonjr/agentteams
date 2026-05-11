# Template Authoring Guide

This guide covers how to write and register templates for the Agent Teams Module. The canonical source is [`templates/AUTHORING-GUIDE.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/AUTHORING-GUIDE.md) in the repository.

---

## Versioning Standards

The canonical version-numbering rule for template-library, template-authoring, and agent-documentation standards lives in the template authoring guide. It does not define Python package release versioning. In short:

- Major versions are for breaking standards changes that make existing compliant templates require migration.
- Minor versions are for backward-compatible additions that add new decision criteria or recommended practice.
- Patch versions are for clarifications, added or repaired examples that only illustrate existing intent, and other non-semantic corrections that do not add new decision criteria.

When a release mixes change types, use the highest-impact bump.

## Placeholder Syntax

Two placeholder syntaxes are used. For full details, see [`templates/PLACEHOLDER-CONVENTIONS.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/PLACEHOLDER-CONVENTIONS.md).

### Auto-Resolved: `{UPPER_SNAKE_CASE}`

Filled automatically by the rendering engine from the project description and manifest.

```
{PROJECT_NAME}         → project_name from the brief
{PRIMARY_OUTPUT_DIR}   → primary_output_dir field
{AGENT_SLUG_LIST}      → multi-line YAML block of all agent slugs (use without brackets)
{STYLE_REFERENCE_PATH} → style_reference field (or {MANUAL:STYLE_REFERENCE_PATH} if null)
{DIAGRAM_TOOLS}        → detected diagram tool(s) e.g. "Mermaid or Graphviz/DOT"
{TOOL_DOCS_URL}        → tool's docs_url from the brief
{TOOL_API_SURFACE}     → tool's api_surface from the brief
{TOOL_COMMON_PATTERNS} → tool's common_patterns from the brief
```

> **Critical:** When `{AGENT_SLUG_LIST}` is used in YAML front matter, do NOT wrap it in brackets. Use `agents:{AGENT_SLUG_LIST}` — the placeholder expands to a multi-line YAML block sequence.

### Manual-Required: `{MANUAL:UPPER_SNAKE_CASE}`

Cannot be auto-resolved. Collected into `SETUP-REQUIRED.md` for the user to fill in.

```
{MANUAL:REFERENCE_DB_PATH}     → path to bibliography database
{MANUAL:STYLE_REFERENCE_PATH}  → path to style guide (when not in brief)
{MANUAL:CONVERSION_PIPELINE}   → pandoc/build command for format conversion
```

---

## Required Sections Per Agent Tier

Every agent template must include these sections in this order:

| Tier | Required Sections |
|------|------------------|
| Orchestrator | YAML front matter; Invariant Core; Workflows; Authority Hierarchy; Domain Agent Routing table |
| Governance | YAML front matter; Invariant Core; Trigger conditions; Procedure; Output format |
| Domain archetype | YAML front matter; Invariant Core; Scope; Procedure (numbered); Constraints; Handoffs |
| Workstream expert | YAML front matter; Invariant Core; Component Specification; Component Brief workflow; Review-and-approve workflow |
| Tool-specific | YAML front matter; Invariant Core; Tool identification; Config management; Invocation; Verification; Cleanup |

### Invariant Core

Every template must have:

```markdown
## Invariant Core

> ⛔ **Do not modify or omit.** The rules below are the immutable contract for this agent.
```

---

## Registering a New Template

1. Create the file in the appropriate subdirectory under `templates/`:
   - `universal/` — governance agents
   - `domain/` — domain archetypes
   - `builder/` — per-framework builder agents
   - Root level — orchestrator and copilot-instructions
2. Add the archetype trigger rule to `agentteams/analyze.py`'s `_ARCHETYPE_TRIGGERS` list
3. Add the new placeholder to `templates/PLACEHOLDER-CONVENTIONS.md` if introducing a new placeholder
4. Add the archetype slug to `selected_archetypes.items.enum` in `schemas/team-manifest.schema.json` (and update `schemas/project-description.schema.json` only if your intake schema explicitly validates allowed archetype labels)
5. Update `template-library-expert.agent.md` sections count

### Post-Production Auditor Registration Notes

For `domain/post-production-auditor.template.md`, use this registration profile:

1. Add an `_ARCHETYPE_TRIGGERS` entry in `agentteams/analyze.py` with conservative keywords for mutation-heavy data projects (`pipeline`, `etl`, `collector`, `mutation`)
2. Add `post-production-auditor` to `selected_archetypes.items.enum` in `schemas/team-manifest.schema.json`
3. Register the template in `agentteams/templates/template-chapter-audit.csv` using a unique `TA-` ID
4. Do not place optional post-production routing rows inside `AGENTTEAMS:BEGIN routing_table_rows`; add them in the user-editable gap below the fence
5. Do not place optional post-production workflows inside `AGENTTEAMS:BEGIN available_workflows`; add them in the user-editable gap before that fence
6. Use `{MANUAL:...}` placeholders for post-production profile values (specifically trigger version, bulk threshold, source-of-truth spec, duplicate cap, and audit slug) unless you also add auto-resolution mappings in `agentteams/analyze.py`

This prevents `--update --merge` from force-propagating post-production routing/workflow content to teams that do not include `@post-production-auditor`.

---

## Placeholder Rules

1. Placeholder names must be `UPPER_SNAKE_CASE`
2. Every auto-resolved placeholder must map to a key in `_build_placeholder_map()` in `agentteams/analyze.py`
3. Never use `{UPPER_SNAKE_CASE}` for a value that cannot be auto-resolved — use `{MANUAL:}` instead
4. Do not introduce new auto-resolved placeholders without adding them to `PLACEHOLDER-CONVENTIONS.md` and `agentteams/analyze.py`
