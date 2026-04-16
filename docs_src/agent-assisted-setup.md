# Agent-Assisted Team Creation

You can use a coding agent — GitHub Copilot, Claude, or any capable chat model — to guide you through team creation interactively. Instead of writing a project description file by hand, you describe your project in plain language and let the agent draft the brief, run the generation command, and walk you through the results.

This page documents the prompt patterns that work best, what to expect at each step, and how to incorporate agent-assisted setup into your workflow.

---

## Overview

The typical agent-assisted workflow has four steps:

```
1. Draft the brief      — "Help me write a project description for..."
2. Generate the team    — "Run agentteams with this brief and review the output"
3. Resolve placeholders — "Help me fill in SETUP-REQUIRED.md"
4. Verify the team      — "Run --post-audit and summarize any findings"
```

A coding agent handles every step. You provide intent; the agent handles syntax, flags, and validation.

---

## Step 1 — Draft the Project Brief

Start by asking the agent to interview you and produce a `brief.json`. The agent will ask clarifying questions and then write a description file suitable as input to `agentteams`.

### Prompt — Interview and draft

```
I want to create an AI agent team for my project using the agentteams module.
Interview me with the minimum number of questions needed to fill out a
project description brief (brief.json). Ask one question at a time.
When you have enough information, write the brief to .github/agents/_build-description.json.
```

The agent will ask about:

- **Project name and goal** — what the project produces and why
- **Deliverable type** — Python modules, Markdown documents, data pipelines, research papers, etc.
- **Output format** — e.g. `Python 3.11 modules`, `PDF reports`, `LaTeX manuscript`
- **Components** — the major units of work (each gets its own workstream expert)
- **Tools and frameworks** — libraries, databases, APIs the project uses
- **Existing codebase?** — whether there is an existing project directory to scan

### Prompt — Direct brief from a README

If your project already has a README, have the agent extract the description automatically:

```
Read the README.md in this project and write a agentteams project description brief
to .github/agents/_build-description.json. Use the project name, goal, deliverables,
and component list from the README. Ask me only if something critical is missing.
```

### What a good brief looks like

```json
{
  "project_name": "SalesDataPipeline",
  "project_goal": "Build an ETL pipeline that ingests daily sales CSV exports, transforms them, loads them into a PostgreSQL warehouse, and produces weekly summary reports.",
  "deliverables": ["Python ETL modules", "SQL scripts", "PDF reports"],
  "output_format": "Python 3.11 modules and PDF reports",
  "primary_output_dir": "src/",
  "components": [
    {"slug": "ingest-stage",   "name": "Ingest Stage",   "number": 1},
    {"slug": "transform-stage","name": "Transform Stage","number": 2},
    {"slug": "load-stage",     "name": "Load Stage",     "number": 3},
    {"slug": "report-stage",   "name": "Report Stage",   "number": 4}
  ],
  "tools": ["pandas", "sqlalchemy", "postgresql", "pytest"]
}
```

See [Description Format](DESCRIPTION-FORMAT.md) for all supported fields.

---

## Step 2 — Generate the Team

Once the brief is written, ask the agent to run the generation command.

### Prompt — Generate and review

```
Run agentteams with the brief at .github/agents/_build-description.json,
targeting this project directory, using the copilot-vscode framework.
Use --dry-run first so I can review what will be generated, then run for real
with --merge --yes after I approve.
```

The agent will run:

```bash
# Preview
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project . \
  --dry-run

# Then, after your approval:
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project . \
  --merge --yes
```

### Prompt — Generate with full audit in one step

```
Generate the agent team from .github/agents/_build-description.json for this project.
Use --post-audit so the audit runs immediately. Use --security-offline to skip the
live vulnerability feed. Summarize the audit findings when done.
```

---

## Step 3 — Resolve Placeholders

After generation, `SETUP-REQUIRED.md` lists any `{MANUAL:*}` placeholders that could not be auto-resolved. The agent can fill most of them from project context.

### Prompt — Fill SETUP-REQUIRED.md

```
Read .github/agents/SETUP-REQUIRED.md. For each pending placeholder, look at the
project source files and README to propose a value. Show me each placeholder and
your proposed value, and ask for confirmation before writing to the agent files.
```

### Prompt — Fill a specific placeholder

```
The placeholder {MANUAL:STYLE_REFERENCE_PATH} is unresolved in the orchestrator
agent. This project uses the Google Python Style Guide. Update the orchestrator
agent file to replace {MANUAL:STYLE_REFERENCE_PATH} with a link to
https://google.github.io/styleguide/pyguide.html.
```

### Prompt — Add project-specific rules to the orchestrator

The `orchestrator.agent.md` has a `USER-EDITABLE` rules section that survives all future `--merge` updates. This is where project conventions belong.

```
In .github/agents/orchestrator.agent.md, find the "Project Rules" section
inside the USER-EDITABLE zone. Add these rules:
- All Python files must pass ruff and mypy before PR merge
- Database migrations require @security clearance
- New API endpoints require an OpenAPI spec update in the same PR
```

---

## Step 4 — Verify the Team

Run the post-generation audit to confirm the team is structurally sound.

### Prompt — Run the audit

```
Run agentteams --post-audit on this project's agent team and explain
any findings that have severity "error" or "warning".
```

The agent runs:

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --project . \
  --post-audit \
  --security-offline
```

### Prompt — Auto-correct audit findings

```
The post-audit found errors. Run agentteams --post-audit --auto-correct
to repair them automatically, then show me a summary of what changed.
```

---

## Keeping the Team Up to Date

As your project evolves, use the agent to merge template updates without losing project-specific content.

### Prompt — Update after a template change

```
The agentteams templates were updated. Run --merge on this project so the
fenced regions are refreshed but my project-specific rules are preserved.
Show me a brief diff of what changed before committing.
```

The agent runs:

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project . \
  --merge --yes
git diff .github/agents/
```

### Prompt — Add a new component

```
I've added a new API module called "webhooks". Add a component to the brief
with slug "webhooks-api", then run --update so a new @webhooks-api-expert
agent is generated without touching the existing agents.
```

The agent will edit the `_build-description.json` to add the component entry, then run:

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --project . \
  --update --yes
```

### Prompt — Check for drift in CI

```
Write a GitHub Actions step that checks whether the agent team is in sync
with the current templates. It should exit with code 1 if any agent files
have drifted, so the CI job fails and prompts a re-run of --merge.
```

The agent will produce a step like:

```yaml
- name: Check agent team drift
  run: |
    agentteams \
      --description .github/agents/_build-description.json \
      --project . \
      --check
```

---

## Migrating a Legacy Team

If your repository has agent files from before fencing was introduced, the agent can run the migration safely.

### Prompt — Migrate and review

```
My agent files were generated before template fencing was introduced.
Run --migrate to upgrade them with a safety snapshot, then show me what
changed and what I need to restore manually.
```

The agent runs:

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project . \
  --migrate

git diff pre-fencing-snapshot HEAD -- .github/agents/orchestrator.agent.md
```

---

## Reference: Prompt Templates

Quick-copy prompts for common tasks.

| Task | Prompt |
|------|--------|
| Draft a brief from scratch | `Interview me and write a agentteams brief to .github/agents/_build-description.json` |
| Draft a brief from README | `Read README.md and write a agentteams brief to .github/agents/_build-description.json` |
| Generate (preview first) | `Run agentteams --dry-run on the brief, then --merge --yes after I approve` |
| Generate with audit | `Run agentteams --post-audit --security-offline and summarize findings` |
| Fill SETUP-REQUIRED.md | `Read SETUP-REQUIRED.md and propose values for each pending placeholder from project context` |
| Add project rules | `Add these rules to the USER-EDITABLE zone of orchestrator.agent.md: [rules]` |
| Update after template change | `Run agentteams --merge --yes, then show me a diff of what changed` |
| Add a component | `Add component [name/slug] to the brief, then run --update to generate the new expert agent` |
| CI drift gate | `Write a GitHub Actions step using agentteams --check to fail the job if agent files have drifted` |
| Migrate legacy files | `Run agentteams --migrate, then show me what changed and what to restore` |
| Audit and auto-correct | `Run agentteams --post-audit --auto-correct and show me what was repaired` |
