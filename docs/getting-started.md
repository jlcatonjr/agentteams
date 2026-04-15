# Getting Started

## Installation

```bash
pip install agentteams
```

Requires **Python 3.11 or later**. No external runtime dependencies — the module uses only the Python standard library.

To install from source:

```bash
git clone https://github.com/jlcatonjr/agentteams
cd agentteams
pip install -e .
```

---

## Write a Project Description

Create `brief.json`:

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

Or use a Markdown brief with section headings:

```markdown
## Project Name
MyProject

## Project Goal
Build a FastAPI backend with authentication and a task management API.

## Deliverables
- Python modules
- OpenAPI docs
```

See [DESCRIPTION-FORMAT.md](https://github.com/jlcatonjr/agentteams/blob/main/docs/DESCRIPTION-FORMAT.md) for all supported fields.

---

## Generate Your Team

```bash
agentteams \
  --description brief.json \
  --project /path/to/your/project \
  --framework copilot-vscode
```

Output is written to `/path/to/your/project/.github/agents/`.

---

## Review SETUP-REQUIRED.md

Open the generated `SETUP-REQUIRED.md`. Any `{MANUAL:*}` placeholders that could not be auto-resolved are listed there with instructions. Fill them in before activating your team.

---

## Activate Your Team

Open your project in VS Code and start a chat session. Invoke `@orchestrator` to begin.

---

## Keeping Your Team Up to Date

When project structure or templates change, update your team without losing manual edits:

```bash
agentteams --description brief.json --project /path/to/project --update
```

Use `--prune` to also remove agents that are no longer part of the taxonomy.

Check for drift without writing:

```bash
agentteams --description brief.json --project /path/to/project --check
```

---

## Running a Post-Generation Audit

```bash
agentteams --description brief.json --project /path/to/project --post-audit
```

This runs static checks (unresolved placeholders, YAML integrity, required-agent coverage). If the `copilot` CLI is available and authenticated, it also runs an AI-powered conflict and presupposition review.
