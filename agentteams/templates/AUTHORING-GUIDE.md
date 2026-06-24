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
2. Every auto-resolved placeholder must be produced by one of the placeholder-map builders: `analyze._build_placeholder_map` in `agentteams/analyze.py`, `render._component_placeholder_map`/`render._tool_placeholder_map`, `ai_bad_habits.build_catalog_placeholders`, `framework_research.build_framework_placeholders`, or `security_refs.build_security_placeholders`
3. Never use `{UPPER_SNAKE_CASE}` for a value that cannot be auto-resolved — use `{MANUAL:}` instead
4. Do not introduce new auto-resolved placeholders without also adding them to `PLACEHOLDER-CONVENTIONS.md` and the appropriate placeholder-map builder (most commonly `_build_placeholder_map` in `agentteams/analyze.py`)

---

## 1a. Section Fencing (Merge Safety)

Templates that contain **multi-line generated sections** (i.e., placeholders that expand to multiple lines) must use fence markers to delimit those sections. This enables `agentteams --merge` to update generated content on re-generation while preserving all user-authored content outside the fenced regions.

**Full specification:** [`FENCE-CONVENTIONS.md`](FENCE-CONVENTIONS.md)

### Quick Reference

```markdown
<!-- AGENTTEAMS:BEGIN section_id v=1 -->
...generated content...
<!-- AGENTTEAMS:END section_id -->
```

### Section Manifest (required when fence markers are used)

Every template that uses fence markers must declare a section manifest immediately after the YAML front matter closing `---`:

```markdown
<!--
SECTION MANIFEST — template-name.template.md
| section_id          | designation   | notes                      |
|---------------------|---------------|----------------------------|
| generated_section   | FENCED        | Rendered from manifest     |
| user_section        | USER-EDITABLE | Team may modify freely     |
-->
```

- **FENCED** — module-owned; updated by `--merge` on re-generation
- **USER-EDITABLE** — team-owned; never modified by `--merge`

### When to fence

- **Yes:** Any `{PLACEHOLDER}` that expands to multiple lines (e.g., `{AUTHORITY_SOURCES_LIST}`, `{WORKSTREAM_SOURCE_MAP}`, `{TOOL_API_SURFACE}`, security data sections)
- **No:** Inline single-value substitutions (e.g., `{PROJECT_NAME}`, `{PRIMARY_OUTPUT_DIR}`)
- **No:** Fixed prose written directly in the template (no placeholder substitution)

### Migrating legacy repositories

Repositories with agent files that pre-date fencing have no `AGENTTEAMS:BEGIN/END` markers. The `--merge` command will skip these files with an advisory warning. To migrate them:

```bash
# One command to implement (safe, reversible):
agentteams --description .agentteams/brief.json \
           --framework copilot-vscode --project /path/to/repo --migrate

# One command to revert (before or after pushing):
agentteams --revert-migration --project /path/to/repo
```

The consumer descriptor for an external project is always `.agentteams/brief.json`. The thin `_build-description.json` stub is reserved for `--self` builds only (see `references/SELF-BUILD-DESCRIPTOR.md`).

`--migrate` creates a `pre-fencing-snapshot` git tag, then runs `--overwrite`. After migration, review `git diff pre-fencing-snapshot HEAD` to restore any project-specific content to the `USER-EDITABLE` zone, then switch to `--merge` for all future updates.

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

`## Invariant Core` is a **bounded region**, not a label — see §3 for the
canonical heading taxonomy and the boundary rule.

### Handoffs

For agents with downstream workflow steps, list handoffs as a numbered procedure or as a table. All agent slug references within the body must use the `@slug` format.

---

## Versioning Standards for Agent-Documentation Rules

This versioning standard applies to changes in the template library, template-authoring rules, and agent-documentation standards. It does not define the release version of the Python package itself.

Use semantic versioning for standards changes:

- **Major version** (`MAJOR.0.0`) for breaking standards changes that require teams to rewrite existing agent docs, migrate fenced sections, change placeholder meaning, or adopt a new incompatible required structure.
- **Minor version** (`MAJOR.MINOR.0`) for backward-compatible additions such as new optional sections, new recommended checks, new non-breaking placeholders, or new guidance that adds decision criteria or recommended practice without forcing rework.
- **Patch version** (`MAJOR.MINOR.PATCH`) for non-semantic clarifications such as wording fixes, typo corrections, tighter explanations, added or repaired examples that only illustrate existing intent, formatting cleanup, or guidance edits that only clarify existing intent without adding new decision criteria.

Choose the version bump by the highest-impact change in the release:

1. If an existing compliant template could become non-compliant without edits, bump major.
2. If existing compliant templates remain valid and the change only adds capability or guidance, bump minor.
3. If the change only clarifies or corrects the standard without changing compliance expectations, bump patch.

Examples:

- Changing fence marker syntax or required section order is a major change.
- Adding a new optional template section or a new backward-compatible placeholder is a minor change.
- Adding or correcting authoring examples is a patch change unless the example introduces a new decision rule or recommended practice.

---

## 3. Canonical Heading Taxonomy

Every emitted agent document follows one heading taxonomy. This makes documents
structurally uniform, addressable by tooling (structural lint, bridge inventory),
and unambiguous about which regions are immutable versus team-owned.

> **Standards impact:** This taxonomy is a **required** structure. Per
> §"Versioning Standards", adopting it is a **major** agent-documentation
> standards change — existing templates must be migrated to conform.

### 3.1 Document spine (all agent tiers)

| Heading | Level | Region | Notes |
|---|---|---|---|
| `# {Agent Name} — {PROJECT_NAME}` | H1 | — | Title; exactly one per file |
| *(intro paragraph)* | — | — | One short paragraph, no heading |
| `## Invariant Core` | H2 | FENCED | Bounded immutable region — see §3.3 |
| `## Project-Specific Notes` | H2 | USER-EDITABLE | Per-project rules/overrides; preserved by `--merge` |

Tier-specific content lives as **H3 subsections inside `## Invariant Core`**.
Optional non-invariant H2 sections may follow `## Project-Specific Notes`.

### 3.2 Canonical per-tier subsections

| Tier | Recommended subsections (in order) |
|------|------------------------------------|
| Orchestrator | Follows its own established partitioned structure (Constitutional Rules; Authority Hierarchy; Domain Agent Routing; Rules; Available Workflows) — it is the **reference exemplar** for this taxonomy and is exempt from the strict §3.1 spine. |
| Governance | Trigger; Procedure; Output Format; Rules |
| Domain archetype | Scope & Responsibility; Procedure; Constraints; Handoffs |
| Workstream expert | Component Specification; Component Brief Workflow; Review & Approve |
| Tool-specific | Tool Identification; Configuration Management; Invocation; Verification; Cleanup |

These section **names** are canonical; heading **depth** is at author
discretion *within* the Invariant Core region (§3.3) — keep it consistent
within a tier. Tool-tier agents may additionally carry FENCED
placeholder-driven sections (Key API Surface, etc.).

### 3.3 The Invariant Core boundary rule

The Invariant Core is a **machine-bounded region**, not a heading convention:
it is exactly the agent file's **FENCED content** — the
`<!-- AGENTTEAMS:BEGIN … -->` … `<!-- AGENTTEAMS:END … -->` regions. Everything
fenced is module-owned: the immutable contract, replaced wholesale on
`agentteams --update --merge`. The merge engine enforces this boundary, so it is
machine-checkable and cannot silently drift.

`## Invariant Core` is the conventional heading opening an agent's primary
fenced region, and the ⛔ banner restates its immutability — but the **fence**,
not the heading, is the authoritative boundary. Automated refactor/cleanup
agents must not alter fenced content.

**Default fencing is automatic.** `emit` wraps the *entire* agent body in one
`content` fence by default, so a template need not individually fence its
`## Invariant Core` section to get a merge-safe boundary — the whole emitted file
is the FENCED region. A per-section manifest plus explicit `AGENTTEAMS:BEGIN/END`
markers (§1a) is therefore an **optional refinement**: use it when you want some
sections fenced and others left as `USER-EDITABLE` within the same file. When the
template declares its own fences, those govern instead of the default whole-body
wrap.

Team-owned content lives **outside** every fence: the `## Project-Specific
Notes` section present in all agent files (§3.1) and, for the orchestrator, its
`project_rules` gap. This is the supported, merge-safe home for project-specific
rules — the first-class alternative to ad-hoc orphan fences.

---

## 4. Registering a New Domain Archetype

To add a new domain archetype (e.g., `data-validator`):

1. **Write the template** → `templates/domain/data-validator.template.md`  
   Follow the structure in an existing template (e.g., `technical-validator.template.md`).

2. **Register the archetype selector rule** → `agentteams/analyze.py`  
   In `select_archetypes()`, add a condition that includes `"data-validator"` in the returned list when appropriate:
   ```python
   if project_type in ("data-pipeline",) and tools_include("validator"):
       archetypes.append("data-validator")
   ```

3. **Register the output file** → `agentteams/analyze.py`  
   In `_plan_output_files()`, the file is registered automatically if the template name matches `{slug}.template.md`. Verify by running `--dry-run`.

4. **Update PLACEHOLDER-CONVENTIONS.md** if new placeholders are introduced.

5. **Write at least one test** in `tests/test_analyze.py` asserting the archetype is selected for the relevant project type.

---

## 5. Tool Classification Tiers

Tools are resources agents **use** — they are never generated as agents. When
adding a tool entry to a project brief, the rendering engine classifies it into
one of three tiers, and the document type depends on the target framework:

| Tier | Criteria | Copilot output | Claude output |
|------|----------|----------------|---------------|
| **Operational** | `needs_specialist_agent: true`, or category in `database`, `cli`, `build-system` | `references/ref-{tool}-reference.md` (operational depth) | `.claude/skills/tool-{tool}.md` (skill) |
| **Reference** | `needs_specialist_agent: false` (default), category in `framework`, `library` | Lightweight `references/ref-{tool}-reference.md` | same |
| **Passive** | `needs_specialist_agent: false`, category in `language`, `other` | Listed in instructions only | Listed in `CLAUDE.md` only |

To force a tool into the operational tier, set `"needs_specialist_agent": true`.

Operational tool docs use the category-specific **`.doc` template** (no YAML
front matter, no handoffs, no agent persona — the body is reused verbatim as a
Copilot reference doc, or wrapped with skill front matter for Claude):
- `tool-database.doc.template.md` — for `database` category
- `tool-cli.doc.template.md` — for `cli` and `deployment` category
- `tool-build-system.doc.template.md` — for `build-system`/`compiler`/`build` category
- `tool-specific.doc.template.md` — fallback for all other operational categories

Reference-tier tools generate a lightweight reference file (no YAML front matter, no agent persona) using:
- `tool-reference.template.md` — all reference-tier tools; written to `references/ref-{tool}-reference.md`

> Legacy `tool-{tool}.agent.md` files from earlier generations are migrated away
> on `--update` (deleted under `--overwrite`, notice-only under `--merge`).

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
| `allowed-tools` | **Mapped** from the VS Code `tools:` list | `read`→`Read`; `search`→`Grep, Glob`; `edit`→`Edit, Write`; `execute`→`Bash`; `todo`→`TodoWrite`; `agent`→`Task`. Falls back to `Bash, Read, Write, Edit` only when the agent declares no `tools:` block. This preserves per-agent least privilege — a read-only governance agent (`tools: ['read', 'search']`) gets `Read, Grep, Glob`, not write/shell. |

VS Code keys **not** passed through verbatim: `user-invokable`, `agents`, `model`, `handoffs`. The `tools:` list is **mapped** to `allowed-tools` (see above), not dropped.

Example Claude Code output (for a `navigator` whose VS Code `tools: ['read', 'search', 'execute']`):
```yaml
---
name: Navigator — MyProject
description: "Navigate the project structure and locate files."
allowed-tools: Read, Grep, Glob, Bash
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
