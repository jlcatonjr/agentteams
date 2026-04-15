# Agent Teams Module

[![PyPI version](https://img.shields.io/pypi/v/agentteams)](https://pypi.org/project/agentteams/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-github--pages-blue)](https://jlcatonjr.github.io/agentteams/)

Generate a complete, coordinated AI agent team for any project — from a single project description file.

---

## What It Does

Given a project description (a `.json` or `.md` brief), the module:

1. **Analyzes** the project goal, deliverables, tools, and components
2. **Selects** the right agent archetypes from a 4-tier taxonomy
3. **Renders** all agent files by filling in project-specific placeholders
4. **Emits** ready-to-use agent files for VS Code Copilot, Copilot CLI, or Claude

The generated team includes:
- 1 **Orchestrator** agent — coordinates all workflows
- 9 **Governance agents** — navigation, security, consistency, cleanup, documentation
- 2–9 **Domain agents** — appropriate archetypes for your deliverable type
- 1 **Workstream Expert** per project component — deep, component-specific knowledge
- 1 **Team Builder agent** — framework-native agent that can regenerate or expand the team from within your framework
- `copilot-instructions.md` — project conventions and routing rules

---

## Quick Start

### 1. Install

```bash
pip install agentteams
```

Or clone for local development (no external dependencies — stdlib Python 3.11+):

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
```

### 2. Write a project description

Create `brief.json` (or `brief.md`):

```json
{
  "project_name": "MyProject",
  "project_goal": "Build a FastAPI backend with authentication and a task management API.",
  "deliverables": ["Python modules", "OpenAPI docs"],
  "output_format": "Python 3.11",
  "primary_output_dir": "src/",
  "components": [
    {"slug": "auth-module", "name": "Authentication Module", "number": 1},
    {"slug": "tasks-api", "name": "Tasks API", "number": 2}
  ]
}
```

### 3. Generate your team

```bash
agentteams \
  --description brief.json \
  --project /path/to/your/project \
  --framework copilot-vscode
```

Or with the script directly:

```bash
python build_team.py \
  --description brief.json \
  --project /path/to/your/project \
  --framework copilot-vscode
```

Output: agent files in `/path/to/your/project/.github/agents/`

### 4. Review SETUP-REQUIRED.md

Fill in any placeholders that couldn't be auto-resolved. Then open VS Code, invoke `@orchestrator`, and start working.

---

## Framework Support

| Framework | Format | Handoffs | Builder Agent |
|-----------|--------|----------|---------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | ✅ | VS Code Copilot `.agent.md` |
| `copilot-cli` | Plain `.md` system prompts | ❌ | CLI prompt `.md` |
| `claude` | Plain `.md` | ❌ | `CLAUDE.md` system prompt |

---

## Construction via Framework Agent (Key Feature)

After generation, a **Team Builder agent** is installed in your project. This is a framework-native agent that can:

- Conduct an intake interview for a new project
- Extend the team with new workstream experts
- Regenerate agent files after project structure changes

**For VS Code Copilot:** Invoke `@team-builder` in chat.  
**For Claude:** Open a Project with the generated `CLAUDE.md` as the system prompt.  
**For Copilot CLI:** Use `copilot` with the generated prompt file.

The builder ensures construction is always facilitated by the target framework itself, enabling the agent to elicit project-specific details interactively before generating files.

---

## Project Description Format

See [schemas/project-description.schema.json](schemas/project-description.schema.json) for the full schema.

Key fields:
- `project_goal` — **(required)** 1–3 sentence description
- `deliverables` — list of deliverable types
- `output_format` — final output format (PDF, Python modules, HTML, etc.)
- `primary_output_dir` — where authored files live
- `components` — one per workstream; each generates a dedicated expert agent
- `authority_sources` — files agents treat as ground truth
- `tools` — languages and frameworks; tools with `needs_specialist_agent: true` get their own agent

Markdown brief format is also accepted — see [examples/research-project/brief.json](examples/research-project/brief.json) for reference.

---

## Agent Taxonomy

### Tier 1: Orchestrator
Coordinates all workflows. Enforces security, consistency, and voice fidelity rules.

### Tier 2: Governance Agents (always generated)
`navigator` · `security` · `code-hygiene` · `adversarial` · `conflict-auditor` · `conflict-resolution` · `cleanup` · `agent-updater` · `agent-refactor`

### Tier 3: Domain Agents (selected by archetype)
| Archetype | Triggered by |
|-----------|-------------|
| `primary-producer` | Always |
| `quality-auditor` | Always |
| `cohesion-repairer` | Writing/documentation projects |
| `style-guardian` | Projects with style references |
| `technical-validator` | Code/data/technical projects |
| `format-converter` | Projects with compiled output (LaTeX, PDF) |
| `reference-manager` | Projects with citation databases |
| `output-compiler` | Multi-component assembly projects |
| `visual-designer` | Projects with diagrams or figures |
| `module-doc-author` | Projects with `pip_package_name` or PyPI distribution |
| `module-doc-validator` | Projects with `pip_package_name` or PyPI distribution |
| `tool-specific` | Tools with `needs_specialist_agent: true` |

### Tier 4: Workstream Experts
One generated per component. Prepares Component Briefs, reviews drafts, issues ACCEPT/REVISE verdicts.

---

## CLI Reference

```
agentteams --help

Options:
  --description PATH   Project description (.json or .md) [required]
  --project     PATH   Project directory to scan
  --framework   NAME   copilot-vscode (default) | copilot-cli | claude
  --output      DIR    Output directory (default: <project>/.github/agents/)
  --dry-run            Show what would be generated without writing
  --overwrite          Overwrite existing files without prompting
  --yes, -y            Non-interactive: skip all prompts
  --no-scan            Disable project directory scanning
  --update             Re-render drifted files AND emit new agents added to the
                       taxonomy since the last build; preserves manually-filled values
  --prune              Used with --update: also delete agents removed from the taxonomy
  --check              Check for template drift and structural changes (exit code 1 if found)
  --scan-security      Scan generated agent files for security issues
  --self               Operate on the module's own agent team
  --version            Print version
```

---

## Maintenance Commands

Once a team has been generated, the module can detect and repair two kinds of drift:

- **Content drift** — a template's text changed (re-renders affected files)
- **Structural drift** — agents were added or removed from the taxonomy (emits new files, reports removed files)

### Check for drift

```bash
agentteams --description brief.json --check
# Exit code 0: no drift. Exit code 1: drift or structural changes detected.
```

### Update drifted files and new agents (preserve manual values)

When the module is updated (e.g., a new governance agent is added), run `--update` to bring an existing team in sync. New agent files are emitted; changed files are re-rendered preserving any `{MANUAL:*}` values you filled in previously; removed agents are reported but not deleted:

```bash
agentteams --description brief.json --update
```

To also delete agents that are no longer part of the taxonomy:

```bash
agentteams --description brief.json --update --prune
```

### Security scan

Scan deployed agent files for PII, credentials, and unresolved placeholders:

```bash
agentteams --description brief.json --scan-security
```

### Self-maintenance

Regenerate the module's own meta-agent team:

```bash
agentteams --self
```

---

## Tool Classification

Tools declared in the brief are classified into three tiers:

| Tier | When | Output |
|------|------|--------|
| **Specialist agent** | `needs_specialist_agent: true` or category = `database`, `deployment`, `pipeline`, `compiler` | Full `.agent.md` with category-specific template |
| **Reference file** | Default for `framework`, `library`, `api`, `cli` | `references/ref-{tool}-reference.md` |
| **Passive** | `language`, `other` | Listed in `copilot-instructions.md` only |

The engine also parses dependency manifests (`requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`) from the project directory to detect tools automatically.

---

## Examples

- [Research project](examples/research-project/brief.json) — academic paper with chapters, LaTeX output, and bibliography
- [Software project](examples/software-project/brief.json) — FastAPI backend with authentication and task API
- [Data pipeline](examples/data-pipeline/brief.json) — ETL pipeline with four workstream components

---

## Running Tests

```bash
python -m pytest tests/ -v
```

---

## Verify Your Install

```bash
agentteams --help             # all flags and usage
man agentteams                # man-page (after system-prefix pip install)
agentteams --version          # confirm installed version
```

Tests require no external dependencies. Integration tests use the bundled examples.

---

## Project Structure

```
agentteams/
├── build_team.py              # CLI entry point
├── src/
│   ├── ingest.py              # Parse project descriptions
│   ├── analyze.py             # Build team manifest
│   ├── render.py              # Render templates
│   ├── emit.py                # Write files to disk
│   └── frameworks/
│       ├── base.py            # Abstract adapter
│       ├── copilot_vscode.py  # VS Code Copilot adapter
│       ├── copilot_cli.py     # Copilot CLI adapter
│       └── claude.py          # Claude adapter
├── templates/
│   ├── universal/             # Governance agent templates (9)
│   ├── domain/                # Domain archetype templates (9 non-tool + 7 tool-specific + 2 doc = 18 total)
│   ├── builder/               # Team Builder agent templates (3)
│   ├── workstream-expert.template.md
│   ├── copilot-instructions.template.md
│   ├── PLACEHOLDER-CONVENTIONS.md
│   └── AUTHORING-GUIDE.md     # Template authoring guide
├── schemas/
│   ├── project-description.schema.json
│   └── team-manifest.schema.json
├── examples/
│   ├── research-project/brief.json
│   ├── software-project/brief.json
│   └── data-pipeline/brief.json
└── tests/
    ├── test_ingest.py
    ├── test_analyze.py
    ├── test_render.py
    ├── test_emit.py
    ├── test_drift.py
    ├── test_scan.py
    └── test_integration.py
```

---

## Documentation

- [templates/AUTHORING-GUIDE.md](templates/AUTHORING-GUIDE.md) — How to write and register new agent templates
- [docs/DESCRIPTION-FORMAT.md](docs/DESCRIPTION-FORMAT.md) — Full field-by-field description format reference
- [.github/agents/references/agent-taxonomy.reference.md](.github/agents/references/agent-taxonomy.reference.md) — Four-tier agent taxonomy specification
- [schemas/project-description.schema.json](schemas/project-description.schema.json) — JSON Schema for project descriptions
- [schemas/team-manifest.schema.json](schemas/team-manifest.schema.json) — JSON Schema for the internal team manifest

---

## License

MIT
