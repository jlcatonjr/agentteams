# Template Authoring Guide

This guide covers how to write and register templates for the Agent Teams Module. The canonical source is [`templates/AUTHORING-GUIDE.md`](https://github.com/jlcatonjr/agentteams/blob/main/templates/AUTHORING-GUIDE.md) in the repository.

---

## Placeholder Syntax

Two placeholder syntaxes are used. For full details, see [`templates/PLACEHOLDER-CONVENTIONS.md`](https://github.com/jlcatonjr/agentteams/blob/main/templates/PLACEHOLDER-CONVENTIONS.md).

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
4. Add the archetype to the `selected_archetypes` enum in `schemas/project-description.schema.json`
5. Update `template-library-expert.agent.md` sections count

---

## Placeholder Rules

1. Placeholder names must be `UPPER_SNAKE_CASE`
2. Every auto-resolved placeholder must map to a key in `_build_placeholder_map()` in `agentteams/analyze.py`
3. Never use `{UPPER_SNAKE_CASE}` for a value that cannot be auto-resolved — use `{MANUAL:}` instead
4. Do not introduce new auto-resolved placeholders without adding them to `PLACEHOLDER-CONVENTIONS.md` and `agentteams/analyze.py`
