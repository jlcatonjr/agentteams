# Agent Teams Module

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

Clone the repository (no external dependencies — stdlib Python 3.11+):

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
pip install -e .
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
| `claude` | Claude Code front matter `.md` | ❌ | Claude Code system prompt |

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

## Usage Examples

### 1. New project — no existing codebase

Write a `brief.json`, then generate. The engine has no project directory to scan, so it works entirely from the description.

```bash
agentteams \
  --description brief.json \
  --framework copilot-vscode
# Output: .github/agents/ in the current directory
```

---

### 2. New project — existing codebase to scan

Pass `--project` to point at an existing directory. The engine scans `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, and `go.mod` to detect tools automatically, and supplements any fields missing from the brief.

```bash
agentteams \
  --description brief.json \
  --project ~/code/myproject \
  --framework copilot-vscode
# Output: ~/code/myproject/.github/agents/
```

To disable scanning (use the brief as-is):

```bash
agentteams --description brief.json --project ~/code/myproject --no-scan
```

---

### 3. Preview before writing (dry run)

Always a safe first step. Prints every file that would be written or overwritten without touching the filesystem.

```bash
agentteams --description brief.json --project ~/code/myproject --dry-run
```

The output lists each file as `[DRY RUN] WRITE` (new) or `[DRY RUN] OVERWRITE` (would replace existing).

---

### 4. Generate for Claude Code or Copilot CLI

Use `--framework` to target a different runtime. All three produce the same agent team from the same brief.

```bash
# Claude Code sub-agents (.claude/agents/*.md with Claude front matter)
agentteams --description brief.json --project ~/code/myproject --framework claude

# GitHub Copilot CLI (plain Markdown system prompts)
agentteams --description brief.json --project ~/code/myproject --framework copilot-cli
```

See [Framework Support](#framework-support) for output format differences.

---

### 5. Custom output directory

Override where agent files are written. Useful for monorepos or when the default `.github/agents/` location isn't appropriate.

```bash
agentteams \
  --description brief.json \
  --output ~/code/myproject/agents
```

---

### 6. Non-interactive / CI mode

Skip all confirmation prompts. Use in scripts, CI pipelines, or `Makefile` targets.

```bash
agentteams --description brief.json --project ~/code/myproject --yes
# or, to overwrite existing files without prompting:
agentteams --description brief.json --project ~/code/myproject --overwrite --yes
```

---

### 7. Post-generation audit

Run static checks (unresolved placeholders, YAML integrity, required-agent coverage) immediately after generation. If the `gh` CLI is authenticated, also runs an AI-powered conflict and presupposition review via GitHub Models.

```bash
agentteams --description brief.json --project ~/code/myproject --post-audit
```

To automatically repair any findings using the standalone `copilot` CLI:

```bash
agentteams --description brief.json --project ~/code/myproject --post-audit --auto-correct
```

---

### 8. Update an existing team after a module upgrade

When `agentteams` is updated, templates may change and new agent types may be added to the taxonomy. Run `--update` to bring an existing team in sync:

- Files whose templates changed are re-rendered, preserving any `{MANUAL:*}` values you filled in.
- New agent types introduced since the last build are emitted as new files.
- Agents removed from the taxonomy are **reported** but not deleted.

```bash
pip install --upgrade agentteams
agentteams --description brief.json --update
```

---

### 9. Update and remove retired agents

If agents were removed from the taxonomy in a module update and you want to clean them up:

```bash
agentteams --description brief.json --update --prune
```

`--prune` only takes effect alongside `--update`. It will delete files for agents that no longer exist in the taxonomy and were not manually created.

---

### 10. Check for drift without writing (CI gate)

Use `--check` as a non-destructive lint step in CI. Exits with code `1` if any template has changed or if the team composition differs from the last build; exits `0` if everything is in sync.

```bash
agentteams --description brief.json --check
```

Example CI step (GitHub Actions):

```yaml
- name: Check agent team is up to date
  run: agentteams --description brief.json --check
```

---

### 11. Security scan on a deployed team

Scan existing agent files for PII paths, hardcoded credentials, bearer tokens, and unresolved `{MANUAL:*}` placeholders:

```bash
agentteams --description brief.json --scan-security
```

Exits with code `1` if any findings are reported. Suitable as a pre-commit or CI gate.

---

### 12. Self-maintenance (regenerate the module's own team)

Regenerate the agent team that governs this module itself, using the stored `_build-description.json`:

```bash
agentteams --self
```

---

## Example Project Briefs

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
