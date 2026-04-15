# Template Authoring Guide

This guide covers how to write and register templates for the Agent Teams Module.

---

## 1. Placeholder Syntax

Two placeholder syntaxes are used. For full details, see [PLACEHOLDER-CONVENTIONS.md](PLACEHOLDER-CONVENTIONS.md).

### Auto-Resolved: `{UPPER_SNAKE_CASE}`

Filled automatically by the rendering engine from the project description and manifest. Use these for any value the engine can derive — project name, output dirs, tool names, etc.

```
{PROJECT_NAME}         → project_name from the brief
{PRIMARY_OUTPUT_DIR}   → primary_output_dir field
{AGENT_SLUG_LIST}      → multi-line YAML block of all agent slugs (use without brackets)
{STYLE_REFERENCE_PATH} → style_reference field (or {MANUAL:STYLE_REFERENCE_PATH} if null)
{DIAGRAM_TOOLS}        → detected diagram tool(s) e.g. "Mermaid or Graphviz/DOT"
{TOOL_DOCS_URL}        → tool's docs_url from the brief (or {MANUAL:TOOL_DOCS_URL} if absent)
{TOOL_API_SURFACE}     → tool's api_surface from the brief (or {MANUAL:TOOL_API_SURFACE} if absent)
{TOOL_COMMON_PATTERNS} → tool's common_patterns from the brief (or {MANUAL:TOOL_COMMON_PATTERNS} if absent)
```

> **Critical:** When `{AGENT_SLUG_LIST}` (or other list placeholders) is used in a YAML front matter field, do NOT wrap it in brackets. Use `agents:{AGENT_SLUG_LIST}` — the placeholder expands to a multi-line YAML block sequence. Wrapping in `agents: [...]` produces invalid YAML.

### Manual-Required: `{MANUAL:UPPER_SNAKE_CASE}`

Cannot be auto-resolved. Collected into `SETUP-REQUIRED.md` for the user to fill in. Use for values that are project-specific and cannot be inferred:

```
{MANUAL:REFERENCE_DB_PATH}     → path to bibliography database
{MANUAL:STYLE_REFERENCE_PATH}  → path to style guide (when not in brief)
{MANUAL:CONVERSION_PIPELINE}   → pandoc/build command for format conversion
```

### Rules

1. Placeholder names must be `UPPER_SNAKE_CASE`
2. Every auto-resolved placeholder must map to a field produced by `_build_placeholder_map()` in `src/analyze.py`
3. Never use `{UPPER_SNAKE_CASE}` for a value that cannot be auto-resolved — use `{MANUAL:}` instead
4. Do not introduce new auto-resolved placeholders without also adding them to `PLACEHOLDER-CONVENTIONS.md` and `src/analyze.py`

---

## 2. Required Sections Per Agent Tier

Every agent template must include the following sections, in this order:

### YAML Front Matter (required in templates)

Templates are always authored with VS Code Copilot YAML front matter. Framework adapters transform this into the target format at render time — see §6 for the per-framework output format.

```yaml
---
name: Agent Name — {PROJECT_NAME}
description: "One-sentence description of the agent's role."
user-invokable: true|false
tools: ['read', 'edit', 'search']     # Use flow sequence — always valid YAML
agents:{AGENT_SLUG_LIST}               # Block sequence via placeholder — no brackets
model: ["Claude Sonnet 4.6 (copilot)"]
handoffs:
  - label: "Action label"
    agent: target-agent-slug
    prompt: "Prompt for the handoff"
    send: false
---
```

Required YAML fields for VS Code Copilot: `name`, `description`, `user-invokable`, `tools`, `model`.

### Invariant Core (required — mark with ⛔ stop-sign emoji)

Each template must contain an `## Invariant Core` section. This section is prefaced with a stop-sign comment:

```markdown
## Invariant Core

> ⛔ **Do not modify or omit.** The rules below are the immutable contract for this agent.
```

The invariant core defines the agent's non-negotiable rules, constraints, and authority. It must not be removed by automated refactoring or cleanup agents.

### Handoffs

For agents with downstream workflow steps, list handoffs as a numbered procedure or as a table. All agent slug references within the body must use the `@slug` format.

---

## 3. Required Sections by Tier

| Tier | Required Sections |
|------|------------------|
| Orchestrator | YAML front matter; Invariant Core; Workflows (numbered step sequences); Authority Hierarchy; Domain Agent Routing table |
| Governance | YAML front matter; Invariant Core; Trigger conditions; Procedure; Output format |
| Domain archetype | YAML front matter; Invariant Core; Scope and responsibility; Procedure (numbered); Constraints; Handoffs |
| Workstream expert | YAML front matter; Invariant Core; Component Specification block; Component Brief workflow; Review-and-approve workflow |
| Tool-specific | YAML front matter; Invariant Core; Tool identification; Configuration management; Invocation procedure; Verification procedure; Cleanup procedure |

---

## 4. Registering a New Domain Archetype

To add a new domain archetype (e.g., `data-validator`):

1. **Write the template** → `templates/domain/data-validator.template.md`  
   Follow the structure in an existing template (e.g., `technical-validator.template.md`).

2. **Register the archetype selector rule** → `src/analyze.py`  
   In `select_archetypes()`, add a condition that includes `"data-validator"` in the returned list when appropriate:
   ```python
   if project_type in ("data-pipeline",) and tools_include("validator"):
       archetypes.append("data-validator")
   ```

3. **Register the output file** → `src/analyze.py`  
   In `_plan_output_files()`, the file is registered automatically if the template name matches `{slug}.template.md`. Verify by running `--dry-run`.

4. **Update PLACEHOLDER-CONVENTIONS.md** if new placeholders are introduced.

5. **Write at least one test** in `tests/test_analyze.py` asserting the archetype is selected for the relevant project type.

---

## 5. Tool Classification Tiers

When adding a tool entry to a project brief, the rendering engine classifies it into one of three tiers:

| Tier | Criteria | Output |
|------|----------|--------|
| **Specialist** | `needs_specialist_agent: true`, or category in `database`, `deployment`, `pipeline`, `compiler` | Full `.agent.md` specialist agent |
| **Reference** | `needs_specialist_agent: false` (default), category in `framework`, `library`, `api`, `cli` | Lightweight `references/ref-{tool}-reference.md` |
| **Passive** | `needs_specialist_agent: false`, category in `language`, `other` | Listed in copilot-instructions only |

To override automatic classification, set `"needs_specialist_agent": true` in the tool definition.

Specialist agents use the category-specific template:
- `tool-database.template.md` — for `database` category
- `tool-cli.template.md` — for `cli` and `deployment` category
- `tool-build-system.template.md` — for `compiler` and `build` category
- `tool-specific.template.md` — fallback for all other specialist categories

Reference-tier tools generate a lightweight reference file (no YAML front matter, no agent persona) using:
- `tool-reference.template.md` — all reference-tier tools; written to `references/ref-{tool}-reference.md`

---

## 6. Per-Framework Agent File Format

Templates are always authored with VS Code Copilot YAML front matter. The framework adapter post-processes template output into the format the target runtime expects. **Never write framework-specific output directly into a template.**

---

### `copilot-vscode` — `.agent.md` with VS Code YAML front matter

Source: [VS Code Copilot agent customization docs](https://code.visualstudio.com/docs/copilot/copilot-customization#_agent-mode-instructions)

The adapter (`agentteams/frameworks/copilot_vscode.py`) validates and supplements front matter. Required fields:

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | `"Agent Name — {PROJECT_NAME}"` |
| `description` | string | Quoted, single sentence |
| `user-invokable` | boolean | `true` for user-facing agents, `false` for governance |
| `tools` | flow sequence | `['read', 'edit', 'search']` — valid YAML flow sequence |
| `model` | flow sequence | `["Claude Sonnet 4.6 (copilot)"]` |

Optional fields:
- `agents:` — block sequence of agent slugs this agent can invoke
- `handoffs:` — list of handoff objects with `label`, `agent`, `prompt`, `send`

If a template has no front matter, or is missing required keys, the adapter injects defaults automatically.

---

### `copilot-cli` — Plain Markdown system prompt

Source: [GitHub Copilot in the CLI docs](https://docs.github.com/en/copilot/github-copilot-in-the-cli/about-github-copilot-in-the-cli)

The adapter (`agentteams/frameworks/copilot_cli.py`) strips all YAML front matter and handoff sections. The output is pure Markdown prose, written to `.github/copilot/<slug>.md`.

| What is stripped | What is preserved |
|-----------------|------------------|
| All YAML front matter (`---` block) | All prose body sections |
| `## Handoff …` heading blocks | All non-handoff headings and content |
| `user-invokable`, `tools`, `model`, `agents` keys | N/A — entire YAML block is removed |

No metadata header of any kind is added. The file is the system prompt verbatim.

---

### `claude` — Claude Code sub-agent format

Source: [Claude Code sub-agents docs](https://docs.anthropic.com/en/docs/claude-code/sub-agents)

The adapter (`agentteams/frameworks/claude.py`) replaces the VS Code YAML block with Claude Code-compatible front matter and writes files to `.claude/agents/<slug>.md`.

Transformation steps applied by the adapter:
1. Extract `name` and `description` from the VS Code YAML before stripping it.
2. Strip the VS Code YAML block entirely (keys are incompatible with Claude Code).
3. Strip `## Handoff …` sections (not supported).
4. Inject a Claude Code front matter block.

Claude Code front matter keys written by the adapter:

| Field | Source | Notes |
|-------|--------|-------|
| `name` | Extracted from VS Code `name:` key | Falls back to slug-derived name |
| `description` | Extracted from VS Code `description:` key | Omitted if blank |
| `allowed-tools` | Fixed default | `Bash, Read, Write, Edit` |

VS Code keys **not** passed through: `user-invokable`, `tools`, `agents`, `model`, `handoffs`.

Example Claude Code output:
```yaml
---
name: Navigator — MyProject
description: "Navigate the project structure and locate files."
allowed-tools: Bash, Read, Write, Edit
---

# Navigator

...body...
```

---

## 7. Validation

Before submitting a new template:

1. Run `python build_team.py --description examples/software-project/brief.json --dry-run` and verify the template is rendered without errors.
2. Run `python -m pytest tests/test_integration.py -v` and verify all snapshot tests pass. If the template changes output, regenerate snapshots with `--overwrite`.
3. Check `SETUP-REQUIRED.md` in the output — no `{MANUAL:}` tokens should appear unexpectedly.
4. **VS Code Copilot only** — Verify YAML front matter is valid:
   ```bash
   python -c "import yaml; yaml.safe_load(open('.github/agents/your-agent.agent.md').read().split('---')[1])"
   ```
   For `copilot-cli` framework output, no YAML front matter is present and this check does not apply.
   For `claude` framework output, YAML front matter is present but uses Claude Code keys (`allowed-tools`) that differ from VS Code format — validate with:
   ```bash
   python -c "import yaml; yaml.safe_load(open('.claude/agents/your-agent.md').read().split('---', 2)[1])"
   ```
