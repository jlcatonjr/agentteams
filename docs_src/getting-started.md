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

---

## Migrating a Legacy Agent Team to Fenced Templates

If your repository has existing agent files generated before fencing was introduced, use `--migrate` to upgrade them in a single, reversible step.

### What `--migrate` does

1. Creates a git tag `pre-fencing-snapshot` at the current HEAD — your safety rollback point.
2. Runs `--overwrite` to regenerate all agent files with fenced templates.
3. Prints a quality-audit checklist with `git diff` commands to review any lost project-specific content.

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project /path/to/project \
  --migrate
```

### After migration: restore project-specific rules

Review the diff for content that was in the old files but is not auto-generated:

```bash
git diff pre-fencing-snapshot HEAD -- .github/agents/orchestrator.agent.md
```

Add any project-specific rules to the `### Rules` → `### <Project> Project Rules` subsection in `orchestrator.agent.md`. This is the `USER-EDITABLE` zone — it survives all future `--merge` runs permanently.

Then commit:

```bash
git add .github/agents/ && git commit -m "chore: fence-migrate agent team"
```

### All future updates use `--merge`

Once migrated, never use `--overwrite` again. Use `--merge` instead:

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project /path/to/project \
  --merge --yes
```

`--merge` updates only the template-fenced regions in each file and leaves all user-authored content untouched.

### Reverting if something goes wrong

```bash
agentteams --revert-migration --project /path/to/project
```

This runs `git reset --hard pre-fencing-snapshot` and deletes the tag, restoring all agent files to their pre-migration state. If you have already pushed the migrated commit to a remote, a manual force-push is required after the revert.
