# Examples

Agent Teams Module ships with three bundled example briefs that demonstrate different project types. All are in `examples/`.

---

## Example 1: Software Project (`examples/software-project/`)

A FastAPI backend with authentication and a task management API.

**Brief summary:**

```json
{
  "project_name": "WebAppBackend",
  "project_goal": "Build a Python FastAPI backend for a task management web application, including REST API endpoints, database models, authentication, and automated tests.",
  "deliverables": ["Python modules", "OpenAPI documentation", "test suite"],
  "output_format": "Python 3.11 modules",
  "primary_output_dir": "src/"
}
```

**What gets generated:**

- Orchestrator + 10 governance agents
- Domain agents: `@primary-producer`, `@quality-auditor`, `@technical-validator`, `@format-converter`
- Workstream experts: one per component (e.g. `@auth-module-expert`, `@tasks-api-expert`)
- Specialist agents for FastAPI and PostgreSQL
- Framework instructions file wired to the full team (`.github/copilot-instructions.md` or `.claude/CLAUDE.md`)

**To run this example:**

```bash
agentteams \
  --description examples/software-project/brief.json \
  --project /tmp/webappbackend \
  --framework copilot-vscode
```

The `expected/` directory shows the agent files this brief produces.

---

## Example 2: Data Pipeline (`examples/data-pipeline/`)

An ETL pipeline that ingests daily sales CSVs, transforms and loads them into a PostgreSQL warehouse, and generates weekly PDF reports.

**Brief summary:**

```json
{
  "project_name": "SalesDataPipeline",
  "project_goal": "Build an ETL pipeline that ingests daily sales CSV exports, validates and transforms them, loads them into a PostgreSQL warehouse, and produces weekly summary reports.",
  "deliverables": ["Python ETL modules", "SQL transformation scripts", "weekly PDF reports"],
  "output_format": "Python 3.11 modules and PDF reports",
  "primary_output_dir": "src/"
}
```

**What gets generated:**

- Full governance + domain agent set
- Specialist agents for PostgreSQL and the ETL build system
- Workstream experts scoped to each pipeline stage
- Reference files for pandas and SQLAlchemy

**To run this example:**

```bash
agentteams \
  --description examples/data-pipeline/brief.json \
  --project /tmp/salespipeline \
  --framework copilot-vscode
```

---

## Example 3: Research Project (`examples/research-project/`)

A peer-reviewed academic paper on multi-agent coordination theory, progressing from an HTML outline through a final LaTeX manuscript.

**Brief summary:**

```json
{
  "project_name": "ResearchPaperProject",
  "project_goal": "Produce a peer-reviewed academic paper on multi-agent coordination theory, progressing from an outline through final LaTeX manuscript.",
  "deliverables": ["HTML chapter drafts", "LaTeX manuscript", "BibTeX bibliography"],
  "output_format": "PDF via LaTeX",
  "primary_output_dir": "html/chapters/"
}
```

**What gets generated:**

- Domain agents include `@reference-manager`, `@format-converter`, and `@quality-auditor`
- Authority hierarchy sourced from academic papers and agent documentation
- Workstream experts per chapter / manuscript section
- Framework instructions file tuned for academic writing conventions

**To run this example:**

```bash
agentteams \
  --description examples/research-project/brief.json \
  --project /tmp/researchpaper \
  --framework copilot-vscode
```

---

## Writing Your Own Brief

See [Description Format](DESCRIPTION-FORMAT.md) for the full `brief.json` schema reference, or browse the [JSON schema](https://github.com/jlcatonjr/agentteams/blob/main/schemas/project-description.schema.json) directly.

The minimum viable brief requires only three fields:

```json
{
  "project_name": "MyProject",
  "project_goal": "one sentence describing what you are building",
  "deliverables": ["list of output types"]
}
```

All other fields are auto-inferred or prompted via `SETUP-REQUIRED.md` after generation.

---

## Format Migration Example

If a team already exists and you want to retarget to another runtime without re-rendering from templates:

```bash
agentteams \
  --convert-from /tmp/webappbackend/.github/agents \
  --framework copilot-cli \
  --output /tmp/webappbackend/.github/copilot
```

This migration path preserves body prose and rewrites only framework wrappers/front matter.
