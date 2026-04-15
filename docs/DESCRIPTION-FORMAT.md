# Project Description Format

This guide documents every field in the project description format used by the Agent Teams Module. For the full JSON Schema, see [schemas/project-description.schema.json](../schemas/project-description.schema.json).

---

## Quick Template

Copy this template and fill in your values. Only `project_goal` is required; all other fields improve the quality of the generated team.

```json
{
  "project_name": "MyProject",
  "project_goal": "One to three sentences describing what this project produces.",
  "deliverables": ["primary deliverable type", "secondary deliverable type"],
  "output_format": "Python 3.11 modules",
  "primary_output_dir": "src/",
  "build_output_dir": "dist/",
  "figures_dir": "docs/figures/",
  "authority_sources": [
    {
      "name": "Source specification",
      "path": "docs/spec.md",
      "scope": "what this source is authoritative for",
      "rank": 1
    }
  ],
  "style_reference": null,
  "reference_db_path": null,
  "reference_key_convention": "AuthorYear",
  "tools": [
    {"name": "Python", "version": "3.11", "category": "language"},
    {"name": "FastAPI", "category": "framework"},
    {
      "name": "PostgreSQL",
      "version": "15",
      "category": "database",
      "config_files": ["docker-compose.yml"],
      "needs_specialist_agent": true
    }
  ],
  "style_rules": [
    "Rule one",
    "Rule two"
  ],
  "components": [
    {
      "slug": "module-name",
      "name": "Human-Readable Module Name",
      "number": 1,
      "output_file": "src/module.py",
      "description": "What this component does.",
      "sections": ["Section 1", "Section 2"],
      "sources": ["SourceKey2024"],
      "cross_refs": ["other-component"],
      "quality_criteria": ["Criterion one", "Criterion two"]
    }
  ]
}
```

---

## Field Reference

### `project_goal` *(required)*

**Type:** string  
**Purpose:** The engine uses this as the top-level description of the project. It appears verbatim in the orchestrator and workstream expert files, and drives project type classification.

**What the engine does with it:** Analyzes keywords to classify the project type (`software`, `research`, `content`, `data-pipeline`, `mixed`), then uses the type to select domain agent archetypes.

**Good:**
```json
"project_goal": "Build a Python FastAPI backend with JWT authentication, task CRUD endpoints, and automated test coverage to 80%."
```

**Too vague:**
```json
"project_goal": "Make a web app."
```

**Common mistake:** Describing _how_ to build rather than _what_ to produce. Focus on the deliverable.

---

### `project_name`

**Type:** string  
**Default:** Derived from the project directory name if not provided.  
**Purpose:** Appears in every agent file name and header.

---

### `deliverables`

**Type:** array of strings  
**Purpose:** Tells the engine what types of artifacts the project produces. Used to select domain archetypes (e.g., LaTeX deliverables trigger `format-converter`; citations in deliverables trigger `reference-manager`).

**Examples:**
```json
"deliverables": ["Python modules", "OpenAPI documentation", "test suite"]
"deliverables": ["HTML chapter drafts", "LaTeX manuscript", "BibTeX bibliography"]
"deliverables": ["Python ETL modules", "SQL scripts", "PDF reports"]
```

---

### `output_format`

**Type:** string  
**Default:** Inferred from project type (e.g., `"Python modules"` for software projects).  
**Purpose:** The final compiled format. Appears in the `format-converter` agent and in copilot-instructions.

---

### `primary_output_dir`

**Type:** string (path)  
**Default:** Inferred from project type (`"src/"` for software, `"html/"` for content).  
**Purpose:** Where authored files live. This is what agents treat as the "source of truth" for primary deliverables.

---

### `build_output_dir`

**Type:** string (path)  
**Default:** `"build/"`  
**Purpose:** Where compiled/built artifacts are written. Agents do not directly author files here.

---

### `figures_dir`

**Type:** string (path)  
**Default:** `"figures/"`  
**Purpose:** Directory for diagram source files and rendered figures. Used by `visual-designer` if included.

---

### `authority_sources`

**Type:** array of objects  
**Purpose:** Files that agents treat as ground truth. The ranking determines which source wins in a conflict.

Each entry:
```json
{
  "name": "Human-readable name",
  "path": "relative/path/to/file",
  "scope": "What this source is authoritative for",
  "rank": 1
}
```

**What the engine does with it:** Builds the authority hierarchy in orchestrator, technical-validator, and conflict-auditor agents. Lower rank numbers = higher authority.

**Common mistake:** Listing sources that don't yet exist. Only list files that are (or will be) present before agents start working.

---

### `style_reference`

**Type:** string (path) or null  
**Default:** null  
**Purpose:** Path to a style guide, voice samples directory, or editorial conventions file. When provided, the `style-guardian` archetype is included in the generated team.

**Effect when null:** `style-guardian` is not generated. The `{STYLE_REFERENCE_PATH}` placeholder resolves to `{MANUAL:STYLE_REFERENCE_PATH}` and appears in `SETUP-REQUIRED.md`.

---

### `reference_db_path`

**Type:** string (path) or null  
**Default:** null  
**Purpose:** Path to a BibTeX `.bib` file or similar citation database. When provided, the `reference-manager` archetype is included and this path populates the agent's database reference.

---

### `reference_key_convention`

**Type:** string  
**Default:** `"AuthorYear"`  
**Purpose:** Citation key format (e.g., `"AuthorYear"`, `"IEEE"`, `"Chicago"`). Appears in `reference-manager` agent instructions.

---

### `tools`

**Type:** array of tool objects  
**Purpose:** Languages, frameworks, libraries, and infrastructure tools used in the project.

Each tool:
```json
{
  "name": "ToolName",
  "version": "1.0",
  "category": "database",
  "config_files": ["path/to/config.yaml"],
  "needs_specialist_agent": true
}
```

**Tool categories and their effects:**

| Category | Default tier | Override with `needs_specialist_agent` |
|----------|-------------|----------------------------------------|
| `language` | Passive (listed only) | — |
| `framework` | Reference file | Set `true` for full agent |
| `library` | Reference file | Set `true` for full agent |
| `cli` | Reference file | Set `true` for full agent |
| `database` | Specialist agent | Set `false` to downgrade |
| `deployment` | Specialist agent | Set `false` to downgrade |
| `pipeline` | Specialist agent | Set `false` to downgrade |
| `compiler` | Specialist agent | Set `false` to downgrade |
| `other` | Passive | Set `true` for full agent |

**Auto-detection:** If `--project` points to an existing directory, the engine also scans `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, and `go.mod` to supplement the tools list.

---

### `style_rules`

**Type:** array of strings  
**Purpose:** Project-specific coding or writing conventions. Written verbatim into governance agents.

**Examples:**
```json
"style_rules": [
  "All public functions must have docstrings",
  "Type annotations required on all public signatures",
  "No mutable default arguments"
]
```

---

### `components`

**Type:** array of component objects  
**Purpose:** One component = one workstream expert agent. Each component maps to a distinct deliverable module, chapter, or subsystem.

Each component:
```json
{
  "slug": "auth-module",
  "name": "Authentication Module",
  "number": 1,
  "output_file": "src/auth/",
  "description": "What this component produces.",
  "sections": ["JWT token validation", "Permission decorators"],
  "sources": ["AuthorYear2024"],
  "cross_refs": ["tasks-api"],
  "quality_criteria": ["All endpoints covered by tests", "No hardcoded secrets"]
}
```

Fields:
- `slug` — machine-readable identifier; becomes the file name `{slug}-expert.agent.md`
- `name` — human-readable label for the agent
- `number` — ordering hint
- `output_file` — path where this component's output is written
- `description` — appears in the workstream expert's invariant core
- `sections` — sub-sections or modules within this component
- `sources` — citation keys or source names this component is grounded in
- `cross_refs` — slugs of other components this one depends on
- `quality_criteria` — acceptance criteria the expert uses to ACCEPT/REVISE

---

## Markdown Format Alternative

If you prefer Markdown input, structure the file with these section headers:

```markdown
## Project Goal
One to three sentences describing the project.

## Deliverables
- Primary deliverable type
- Secondary deliverable type

## Output Format
Python 3.11 modules

## Primary Output Directory
src/

## Tools
- Python 3.11
- FastAPI (framework)
- PostgreSQL 15 (database, specialist agent required)

## Style Rules
- All public functions must have docstrings

## Components
- auth-module: Authentication Module
- tasks-api: Tasks API
```

The Markdown parser uses section headings to identify fields. Unrecognized sections are appended to `project_goal`. JSON format is recommended for complex projects.

---

## Common Mistakes

| Mistake | Effect | Fix |
|---------|--------|-----|
| Omitting `components` | No workstream experts generated | Add one component per major deliverable |
| Using `needs_specialist_agent: true` on `language` tools | Generates an agent for "Python" | Set only on tools with real operational complexity |
| Setting `reference_db_path` when no bib file exists | reference-manager agent generated but non-functional | Set to null if the bib file doesn't exist yet |
| Component slugs with spaces | Invalid file names | Use hyphens: `auth-module`, not `auth module` |
| Long `project_goal` | Agent files become verbose | Keep to 1–3 sentences; use `components` for detail |
