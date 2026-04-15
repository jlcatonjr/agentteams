# How It Works

Agent Teams Module transforms a single project description into a complete, coordinated AI agent team through a four-stage pipeline.

---

## Pipeline Overview

```
brief.json / brief.md
        │
        ▼
   ┌─────────┐
   │  ingest  │  Parse and validate the project description
   └────┬────┘
        │ normalized description dict
        ▼
   ┌─────────┐
   │ analyze  │  Select archetypes, detect tools, build manifest
   └────┬────┘
        │ team manifest dict
        ▼
   ┌────────┐
   │ render  │  Fill templates with resolved placeholders
   └────┬───┘
        │ (output_path, rendered_content) pairs
        ▼
   ┌──────┐
   │ emit  │  Write agent files to .github/agents/
   └──────┘
```

### Stage 1 — Ingest (`agentteams/ingest.py`)

Reads your project description from a `.json` or `.md` file and validates it against `schemas/project-description.schema.json`. Returns a normalized Python dict.

### Stage 2 — Analyze (`agentteams/analyze.py`)

Builds the **team manifest** from the description dict. Key decisions made in this stage:

- **Project type classification** — determines whether the project is software, writing, data-pipeline, research, or documentation
- **Archetype selection** — picks the right domain agent mix from the template library
- **Tool importance classification** — decides whether each tool gets a specialist agent, a reference file, or no dedicated agent
- **Authority hierarchy construction** — orders sources by rank for the agents to cite
- **Placeholder resolution** — auto-fills all `{UPPER_SNAKE_CASE}` tokens; flags `{MANUAL:*}` tokens for human review

### Stage 3 — Render (`agentteams/render.py`)

Loads each template from `templates/` and substitutes every `{PLACEHOLDER}` with its resolved value from the manifest. Returns a list of `(relative_path, content)` pairs.

### Stage 4 — Emit (`agentteams/emit.py`)

Writes all rendered files to the target project's `.github/agents/` directory (or equivalent). Generates `SETUP-REQUIRED.md` for any unresolved manual placeholders, and runs the post-generation audit and security scan.

---

## 4-Tier Agent Taxonomy

Every generated team contains agents from four hierarchical tiers.

```
Tier 1: Orchestrator
   └── Routes all work; enforces constitutional rules;
       opens and closes every multi-agent session

Tier 2: Governance Agents
   └── Navigator, Security, Code-Hygiene, Adversarial,
       Conflict-Auditor, Conflict-Resolution, Cleanup,
       Agent-Updater, Agent-Refactor
       Each owns a cross-cutting concern (structure, safety,
       consistency, documentation) rather than a deliverable

Tier 3: Domain Agents
   └── Primary-Producer, Quality-Auditor, Technical-Validator,
       Format-Converter, Reference-Manager, Output-Compiler, ...
       Each owns a production workflow (drafting, auditing,
       converting, compiling)

Tier 4: Workstream Experts
   └── One per project component (e.g. @auth-module-expert,
       @tasks-api-expert)
       Each owns one deliverable unit end-to-end:
       component brief → domain agent commission → review
```

### How Tiers Interact

```
User Request
    │
    ▼
Orchestrator ──► Workstream Expert (prepares Component Brief)
    │                  │
    │                  ▼
    │           Domain Agent (executes production)
    │                  │
    ▼                  ▼
Governance Agents (audits, reviews, clearances)
```

The orchestrator **routes without producing**. Domain agents **produce without scoping**. Workstream experts **scope without producing**. Governance agents **audit without producing**.

---

## Template Library

Templates in `templates/` are Markdown files with `{PLACEHOLDER}` tokens. The library provides:

| Tier | Templates |
|------|-----------|
| Orchestrator | `universal/orchestrator.template.md` |
| Governance | `universal/` (9 templates) |
| Domain | `domain/` (9 archetype templates + 6 tool templates) |
| Workstream Expert | `workstream-expert.template.md` (one template, rendered per component) |
| Builder | `builder/` (3 framework-specific team-builder templates) |

See [Template Authoring](template-authoring.md) for placeholder conventions and authoring rules.

---

## Framework Adapters

The same template library targets three frameworks via adapters in `agentteams/frameworks/`:

| Framework | Agent Format | Entry Point |
|-----------|-------------|-------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | VS Code Copilot agent panel |
| `copilot-cli` | Plain `.md` system prompts | `gh copilot` CLI |
| `claude` | Plain `.md` + `CLAUDE.md` | Claude Projects |

Each adapter in `agentteams/frameworks/` knows the file naming conventions, front-matter schema, and handoff syntax for its target framework.
