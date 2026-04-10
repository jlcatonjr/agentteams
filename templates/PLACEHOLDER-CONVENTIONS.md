# Placeholder Conventions

All templates in this directory use two placeholder syntaxes:

## Auto-Resolved Placeholders

```
{PLACEHOLDER_NAME}
```

These are filled automatically by the rendering engine from the project description and analysis manifest. Examples:

- `{PROJECT_NAME}` — The project name
- `{PROJECT_GOAL}` — One-sentence project goal
- `{PRIMARY_OUTPUT_DIR}` — Path to the primary output directory
- `{AGENT_SLUG_LIST}` — Comma-separated list of all agent slugs
- `{DOMAIN_AGENT_SLUGS}` — Comma-separated list of domain agent slugs
- `{WORKSTREAM_EXPERT_SLUGS}` — Comma-separated list of workstream expert slugs
- `{AUTHORITY_HIERARCHY}` — Formatted authority hierarchy
- `{DELIVERABLE_TYPE}` — Primary deliverable type (e.g., "code", "documents", "data")
- `{STYLE_REFERENCE}` — Style/standards reference description

## Manual-Required Placeholders

```
{MANUAL:PLACEHOLDER_NAME}
```

These cannot be inferred from the project description and require human completion. They are collected into `SETUP-REQUIRED.md` when the team is generated. Examples:

- `{MANUAL:SPECIFIC_FILE_PATHS}` — Exact paths within the project codebase
- `{MANUAL:API_ENDPOINT_NAMES}` — Proprietary API endpoints
- `{MANUAL:STYLE_EXEMPLARS}` — Specific style example documents
- `{MANUAL:EXTERNAL_REPO_PATHS}` — Exact paths to external read-only repositories

## Rules

1. Placeholder names are UPPER_SNAKE_CASE
2. Every placeholder must appear in at least one template
3. Auto-resolved placeholders must map to a field in `team-manifest.schema.json`
4. Manual placeholders must have a description in `SETUP-REQUIRED.md` generation logic
5. Nested placeholders are not supported — `{FOO_{BAR}}` is invalid
