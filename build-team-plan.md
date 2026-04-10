# Agent Team Builder — Implementation Plan

**Repository:** `agentteams/`  
**Date:** April 10, 2026  
**Purpose:** A module that auto-generates a fully configured CoPilot agent team for any specified project, following the structural and constitutional patterns established in *Agent Teams: A Theoretically Grounded Approach*.

---

## 1. Executive Summary

This module accepts a project description and a target project directory, then generates a complete, ready-to-use agent team. The team follows a four-tier architecture (orchestrator → functional/governance agents → domain agents → workstream experts) derived from the book project framework. The output is a set of `.agent.md` files, a `copilot-instructions.md`, and supporting reference scaffolding, written directly into the target project's `.github/agents/` directory. The module is designed to be framework-agnostic: initial support targets VS Code GitHub Copilot, with adapters planned for Copilot CLI and Claude Projects.

---

## 2. Goals

1. **Single command** — `build_team --project /path/to/project --description brief.md` produces a working agent team.
2. **Pattern fidelity** — Generated teams follow the constitutional and structural patterns of the framework (invariant cores, authority hierarchies, handoff protocols, living document policy).
3. **Framework extensibility** — Adapter interface makes it straightforward to add new target frameworks.
4. **Minimal configuration** — The module infers as much as possible from the project description; the user provides only what cannot be inferred.
5. **Two input modes** — (a) project description document, or (b) path to existing project plus a short summary.

---

## 3. Agent Taxonomy

The framework defines four tiers of agents. The module generates the full stack for every project.

### Tier 1 — Orchestrator

A single agent that coordinates all other agents. The orchestrator owns all cross-cutting workflows, enforces security, routes domain work, and closes every multi-file session with a consistency check. It never performs domain-specific work directly.

**Key responsibilities:**
- Define all named workflows with explicit step sequences
- Enforce the authority hierarchy (source truth → published references → instructions → agent docs → primary outputs → generated artifacts)
- Require `@security` clearance before any destructive operation
- Close every multi-file session with `@conflict-auditor`
- Route domain work to the correct domain agent, never handle it directly

### Tier 2 — Functional/Governance Agents (Universal)

These nine agents are included in every team regardless of project type. They address cross-cutting concerns that every project shares.

| Slug | Role | Access |
|------|------|--------|
| `navigator` | Project structure mapping, file location queries, dependency tracking | Read-only |
| `security` | Highest-priority sentinel; clears destructive operations, credential exposure, external writes | Read-only |
| `adversarial` | Presupposition critic; challenges plans before execution; traces cascade dependencies | Read-only |
| `conflict-auditor` | Detects logical inconsistencies across all output files; logs findings | Read + log |
| `conflict-resolution` | Makes ACCEPT/REJECT/REVISE decisions on flagged conflicts | Read + edit |
| `cleanup` | Removes stale artifacts; requires security clearance; applies four safety checks before deletion | Edit |
| `agent-updater` | Synchronizes agent documentation when project structure or content changes | Edit |
| `agent-refactor` | Extracts shared data to reference files; enforces spec compliance | Edit |
| `source-gatherer` *(optional)* | Retrieves and stages external source materials for domain agents | Execute |

**Constitutional rules for governance agents:**
- `@security` must be invoked before ANY file deletion, bulk edit (≥3 files), external repo write, or credential-adjacent content
- `@conflict-auditor` must be invoked after every multi-file change session
- `@adversarial` must be invoked before executing any plan that involves irreversible or cross-cutting changes
- Governance agents never perform domain-specific content work

### Tier 3 — Domain Agents (Project-Adapted)

Domain agents perform the project's actual work. The set is determined by the project type and the deliverable/process profile extracted from the project description. Each domain agent maps to one of these seven archetypes:

| Archetype | Book Equivalent | General Role |
|-----------|----------------|--------------|
| **Primary Producer** | `book-content-expert` | Creates the primary project deliverable (text, code, data, reports) |
| **Quality Auditor** | `expert-critic` | Read-only; identifies structural weaknesses, quality defects, pattern violations |
| **Cohesion Repairer** | `argument-weaver` | Repairs structural/flow problems in primary deliverable outputs |
| **Style/Standards Guardian** | `voice-style` | Enforces project-specific style, naming, or coding conventions |
| **Technical Validator** | `code-hygiene` | Verifies technical accuracy of claims, code, or specifications |
| **Format/Output Converter** | `html-document-builder` | Transforms primary output to secondary distribution formats |
| **Reference/Dependency Manager** | `bibliography-manager` | Manages the project's reference database, dependency tracking, or citation integrity |
| **Output Compiler** | `latex-specialist` | Assembles and compiles the final deliverable |
| **Visual Designer** | `graphviz-expert` | Generates diagrams, figures, or visualizations |

The module selects the appropriate subset of archetypes based on the project profile. Not every project needs all nine; a software project may not need a Reference/Dependency Manager archetype but will always need a Technical Validator and Style/Standards Guardian.

### Tier 4 — Workstream Expert Agents (Instantiated Per Component)

One expert agent is instantiated for each major project component (analogous to chapter experts in the book project). These agents are the **intellectual architects** of their domain: they read sources, organize arguments or specifications, prepare briefs for the Primary Producer, and judge whether the Producer's output meets the component's obligations.

**Characteristics:**
- One per major workstream or project component
- Not user-invokable directly; invoked by the orchestrator or Primary Producer
- Owns the component's thesis/specification, evidence inventory, cross-reference obligations, and quality criteria
- Commissions work from the Primary Producer; reviews and approves output
- Maintains an "Invariant Core" section specifying the component specification that cannot be changed without orchestrator approval

**Instantiation rule:** Every component that has a distinct deliverable, distinct sources, or distinct quality criteria gets its own workstream expert. Components that merely subdivide an existing expert's scope do not.

### Tool-Specific Agents

When a project uses specific tools that require their own task coordination (e.g., a database migration tool, a deployment pipeline, a specific API), a tool-specific agent can be generated. Tool-specific agents are a sub-category of domain agents. They own the configuration, invocation, and verification of a specific tool within the project.

---

## 4. Agent File Anatomy

Every generated agent file follows this structure:

```markdown
---
name: {Agent Name} — {Project Name}
description: "{One-sentence description}"
user-invokable: true|false
tools: ['read', 'edit', 'search', 'execute', 'todo', 'agent']
agents: [downstream-agent-slugs]
model: ["Claude Sonnet 4.6 (copilot)"]
handoffs:
  - label: {Handoff Label}
    agent: {agent-slug}
    prompt: "{Handoff prompt template}"
    send: false
---

# {Agent Name} — {Project Name}

## Purpose
{One paragraph: what this agent does and what it does NOT do}

## Invariant Core
> ⛔ Do not modify or omit. [Immutable responsibilities and rules]

## Responsibilities / Workflow
{Numbered steps or table}

## Rules
{Bullet list of constitutional and operational rules}
```

**Key structural invariants across all generated files:**
- Every agent has an **Invariant Core** block that cannot be modified without orchestrator approval
- Every agent has explicit **handoff** entries for every downstream agent it delegates to
- Every agent has a **Return to Orchestrator** handoff
- Governance agents are **read-only** (no `edit` or `execute` tools) unless their function requires modification
- Domain agents reference their governance counterparts by slug, never inline the governance logic

---

## 5. Module Architecture

### 5.1 Directory Layout

```
/agentteams/
├── .github/
│   └── agents/                    # The module's own meta-agents (for team generation work)
├── templates/
│   ├── universal/                 # Governance agent templates (always included)
│   │   ├── orchestrator.template.md
│   │   ├── navigator.template.md
│   │   ├── security.template.md
│   │   ├── conflict-auditor.template.md
│   │   ├── conflict-resolution.template.md
│   │   ├── adversarial.template.md
│   │   ├── cleanup.template.md
│   │   ├── agent-updater.template.md
│   │   └── agent-refactor.template.md
│   ├── domain/                    # Domain agent archetype templates
│   │   ├── primary-producer.template.md
│   │   ├── quality-auditor.template.md
│   │   ├── cohesion-repairer.template.md
│   │   ├── style-guardian.template.md
│   │   ├── technical-validator.template.md
│   │   ├── format-converter.template.md
│   │   ├── reference-manager.template.md
│   │   ├── output-compiler.template.md
│   │   └── visual-designer.template.md
│   ├── workstream-expert.template.md   # Instantiated once per project component
│   └── copilot-instructions.template.md
├── schemas/
│   ├── project-description.schema.json  # JSON Schema for structured brief input
│   └── team-manifest.schema.json        # Internal representation of a generated team
├── src/
│   ├── ingest.py                  # Parse and normalize project description input
│   ├── analyze.py                 # Classify project, extract domains/workstreams/tools
│   ├── select.py                  # Select domain agent archetypes for the project profile
│   ├── render.py                  # Populate templates with analyzed project data
│   ├── emit.py                    # Write rendered files to target directory
│   └── frameworks/
│       ├── base.py                # Abstract adapter interface
│       ├── copilot_vscode.py      # VS Code GitHub Copilot adapter
│       ├── copilot_cli.py         # Copilot CLI (headless) adapter
│       └── claude.py              # Claude Projects adapter
├── examples/
│   ├── software-project/          # Example brief + expected output for software project
│   ├── research-project/          # Example brief + expected output for research project
│   └── data-pipeline/             # Example brief + expected output for data pipeline
├── build_team.py                  # CLI entry point
├── build-team-plan.md             # This file
├── build-team-steps.csv           # Detailed implementation steps
└── README.md
```

### 5.2 Input Formats

**Mode A: Project Description Document**

The primary input is a Markdown or plain-text document structured around these sections (all sections are optional except *Project Goal*):

```markdown
# Project Name

## Project Goal
[One paragraph stating what the project produces and why]

## Deliverables
[List of primary outputs the project produces]

## Processes
[Key workflows or recurring operations]

## Domains
[Subject-matter areas or functional domains the project covers]

## Tools
[Specific tools, frameworks, languages, or platforms used]

## Authority Sources
[What constitutes ground truth? e.g., source code, published papers, specifications]

## Style / Standards  
[Any coding standards, writing style guides, naming conventions]

## Team Members / Roles  (optional)
[Human team members and their roles, for context]
```

**Mode B: Existing Project + Summary**

When `--project` points to an existing non-empty directory, the module supplements the description with:
- Directory structure scan (identifies file types, existing conventions)
- `README.md` extraction if present
- `.gitignore` patterns (infers excluded artifact types)
- Existing `.github/agents/` files if any (avoids re-generating what exists)

### 5.3 Processing Pipeline

```
Input (description file or existing project)
    ↓
[ingest.py]
    Normalize document → structured dict
    Extract: project_name, goal, deliverables, processes, domains, tools, authority_sources, style_rules
    ↓
[analyze.py]
    Classify project type → one of: software, content, research, data-pipeline, mixed
    Identify workstreams → list of {name, description, primary_sources, quality_criteria}
    Identify tool dependencies → list of {tool_name, category, config_files}
    Build authority hierarchy → ranked list of truth sources
    ↓
[select.py]
    Select domain agent archetypes from profile
    Determine which workstreams need dedicated expert agents
    Generate team manifest (JSON) → team-manifest.schema.json
    ↓
[render.py]
    Populate each template with manifest data
    Apply framework-specific adapter transforms
    ↓
[emit.py]
    Write .github/agents/*.agent.md
    Write copilot-instructions.md
    Write .github/agents/references/ scaffold
    Report: files written, placeholders requiring manual completion
```

### 5.4 Framework Adapters

Each framework adapter transforms the rendered agent files to the target format:

| Framework | Output Format | Key Differences |
|-----------|--------------|-----------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | Full YAML front matter; `handoffs:` blocks; `agents:` list; model field |
| `copilot-cli` | `.md` prompt files, `copilot-instructions.md` | No YAML front matter; no handoffs block; structured as system prompt |
| `claude` | `CLAUDE.md` + individual agent system prompts | Claude Projects format; `<agent>` XML blocks for sub-agents |

The base adapter interface (`src/frameworks/base.py`) defines:
- `render_agent_file(agent_manifest) → str` — Render a single agent to its target format
- `render_instructions(project_manifest) → str` — Render project instructions
- `get_file_extension() → str` — Returns the file extension for agent files
- `supports_handoffs() → bool` — Whether the format supports structured handoff blocks

### 5.5 Placeholder Resolution Strategy

Templates contain two classes of placeholders:
- **Auto-resolved** — Filled by the analysis pipeline from the project description (project name, goal, deliverable names, workstream names, tool names, authority sources)
- **Manual-required** — Flagged for the user because they require project-specific knowledge the module cannot infer (e.g., specific file paths within an existing codebase, proprietary API names, organizational style rules not captured in the description)

The `emit.py` step produces a `SETUP-REQUIRED.md` listing every manual-required placeholder that was left unfilled, with instructions for completion.

---

## 6. Implementation Phases

### Phase 0 — Repository & Infrastructure Setup
Establish the module repository structure, CI configuration, and development environment.

### Phase 1 — Template Library
Build the complete template set for all four tiers:
- 9 universal governance agent templates (fully portable — same across all projects)
- 9 domain archetype templates (parameterized — adapted per project)
- 1 workstream expert template (instantiated N times per project)
- 1 orchestrator template (highest complexity)
- 1 `copilot-instructions.md` template

Priority: Get the universal governance templates right first — they are the most reusable.

### Phase 2 — Input Schema & Ingestion
Define and implement the input handling layer:
- `project-description.schema.json` — JSON Schema validating a structured description
- `ingest.py` — Parse Markdown/plain-text descriptions into the schema
- Mode B support — Directory scanning for existing projects
- Input validation with clear user-facing error messages

### Phase 3 — Analysis Engine
Build the classification and extraction logic:
- `analyze.py` — Project type classification, workstream identification, tool extraction
- Authority hierarchy builder
- Workstream-to-expert mapping rules (when does a workstream earn its own expert?)
- Team manifest generator (`team-manifest.schema.json`)

### Phase 4 — Rendering Engine
Build template population and validation:
- `render.py` — Placeholder substitution with the manifest
- Auto-resolved vs. manual-required placeholder tracking
- Validation: all cross-references between agent files are consistent

### Phase 5 — Framework Adapters
Implement the three initial adapters:
- `copilot_vscode.py` — Primary target; full YAML front matter + handoffs
- `copilot_cli.py` — Headless Copilot; system-prompt format
- `claude.py` — Claude Projects format

### Phase 6 — CLI Entry Point
Build `build_team.py`:
- `--project PATH` — Target project directory (empty or existing)
- `--description FILE` — Project description file
- `--framework NAME` (default: `copilot-vscode`)
- `--dry-run` — Print what would be written without writing
- `--overwrite` — Overwrite existing agent files (requires confirmation unless `--yes`)
- Input validation, error messages, and `SETUP-REQUIRED.md` generation

### Phase 7 — Testing & Examples
- Unit tests for each pipeline stage
- Integration tests using the three example projects
- Validate: output files parse cleanly, handoff references are consistent, all placeholders resolved or flagged

### Phase 8 — Documentation
- `README.md` — Quick start, full CLI reference, description format guide
- `examples/` — Three annotated examples (software, research, data-pipeline)
- Template authoring guide (for extending the template library)

---

## 7. Invariant Constitutional Rules for ALL Generated Teams

The following rules are embedded in every generated orchestrator and are not configurable:

1. **`@security` before destructive operations** — File deletions, bulk edits (≥3 files), external repo writes, credential-adjacent content
2. **`@conflict-auditor` after multi-file sessions** — Every session that modifies 2+ files must close with a conflict audit
3. **`@adversarial` before plan execution** — Plans involving irreversible or cross-cutting changes require presupposition review
4. **Never fabricate references** — Every citation, file path, or cross-reference must be verified before insertion
5. **Primary output files are the only directly authored output** — All other files are either generated artifacts or governance documents
6. **Domain agents own their scope** — The orchestrator routes; it does not perform domain work directly
7. **Living document policy** — No stale content in agent docs: no dated audit snapshots, no resolved-issue archaeology, no hardcoded volatile state
8. **Workstream experts commission, they do not write** — The expert briefs the producer; the producer writes; the expert reviews

---

## 8. Non-Functional Requirements

- **Python 3.11+** — No external dependencies for core functionality (stdlib only for ingest, analyze, render, emit)
- **Optional LLM assist** — `--llm-assist` flag enables an LLM call to improve analysis quality (requires API key; not required for basic operation)
- **Idempotent** — Running the module twice on the same input produces the same output
- **Safe by default** — `--overwrite` is not assumed; the module never silently clobbers existing agent files
- **Portable templates** — Templates are plain Markdown with `{PLACEHOLDER}` syntax; readable and editable without the module
- **Framework adapter isolation** — Adding a new target framework requires only adding a new file in `src/frameworks/`; no changes to core pipeline

---

## 9. Key Design Decisions

### Why template-based rather than LLM-generated?
Templates guarantee structural correctness (invariant cores, YAML syntax, handoff blocks) even without LLM involvement. The module must work reliably without an API key. LLM assist is a quality enhancement, not a dependency.

### Why keep governance agents identical across projects?
The governance agents (navigator, security, adversarial, conflict-auditor, conflict-resolution, cleanup, agent-updater, agent-refactor) address concerns that are structurally invariant: every project needs security review, consistency checking, and documentation synchronization. Making them project-specific would introduce per-project drift and reduce the constitutional guarantees the framework is designed to provide.

### Why separate workstream experts from domain agents?
Domain agents own *how work is done* (writing, auditing, converting). Workstream experts own *what is being established* in a specific component. This separation allows the same domain agent (e.g., the Primary Producer) to serve many workstreams without encoding workstream-specific logic into a generic agent, and it allows subject expertise to scale independently of writing/coding capability.

### Why support multiple frameworks?
The framework's constitutional rules (security-first, consistency-close, presupposition-challenge) are valuable regardless of which LLM or agent runtime is used. Framework adapters let those rules travel with the team.

---

## 10. Appendix — Agent Archetype Selection Rules

| Project Characteristic | Archetypes Added |
|-----------------------|-----------------|
| Has written deliverables (docs, reports, chapters) | Primary Producer, Quality Auditor, Style/Standards Guardian, Cohesion Repairer |
| Has code deliverables | Primary Producer, Technical Validator, Style/Standards Guardian (linting) |
| Has external references/citations | Reference/Dependency Manager |
| Has multiple output formats | Format/Output Converter, Output Compiler |
| Has visual/diagram requirements | Visual Designer |
| Has > 3 distinct workstreams | One Workstream Expert per distinct workstream |
| Uses a specific pipeline tool | Tool-Specific Agent per tool |

---

*End of plan document.*
