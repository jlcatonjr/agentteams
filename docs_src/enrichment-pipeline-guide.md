# Enrichment Pipeline

## When to Use This Guide

Read this guide if you:

- Have unresolved `{MANUAL:...}` placeholders in your generated agent files and want to reduce them automatically
- Want to improve tool reference quality by supplementing sparse metadata with data fetched from live sources
- Are running `--post-audit` and want to understand why `--enrich` is implicitly enabled

---

## What Enrichment Does

After generation, agent files may contain unresolved `{MANUAL:...}` placeholders for values that could not be auto-filled from the project description alone. Enrichment is a post-generation pass that resolves as many of these as possible using three methods:

1. **Rule-based fills** — Pattern-match the token name against known default rules
2. **Project context fills** — Scan the existing project directory and Jupyter notebooks for context
3. **AI-powered fills** — Use `--post-audit` to request LLM-assisted completion for tokens that the rule engine cannot resolve

---

## Running Enrichment

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --enrich
```

Enrichment runs after generation and before file emission. The enriched content is written in place.

Dual-mode placeholder policy:
- Default module runs prioritize usability and replace selected optional MANUAL placeholders with explicit `N/A` defaults.
- Strict runs preserve unresolved MANUAL tokens for governance review. Strict mode is on by default for `--self` and can be toggled explicitly.

### With Post-Audit (AI-Powered Fills)

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --enrich --post-audit
```

`--post-audit` implies `--enrich` automatically. Running `--post-audit` without `--enrich` would expose the audit to unresolved placeholders, which generate false positives. The pipeline sets `--enrich = True` if `--post-audit` is requested and `--enrich` was not explicitly set.

---

## Enrichment Phases

### Phase 1 — Defaults Scan

Scans rendered file content for:
- `{MANUAL:...}` tokens that remain unresolved
- `{UPPER_SNAKE_CASE}` tokens that should have been auto-resolved but weren't
- Empty tool metadata fields (`docs_url`, `api_surface`, `common_patterns`)

Results are written to `references/defaults-audit.csv`.

### Phase 2 — Rule-Based Fills

Applies known fill rules for standard token patterns:

| Token | Rule |
|---|---|
| `{MANUAL:REFERENCE_DB_PATH}` | Returns `N/A — no citation database configured for this project` when no `reference_db` is set in the description |
| `{MANUAL:STYLE_REFERENCE_PATH}` | Returns `N/A — no formal style guide defined for this project` or the value from `style_reference` if present |
| `{MANUAL:CONVERSION_PIPELINE}` | Returns `N/A — [format] files are the final deliverable format` when source and output formats match |
| `{MANUAL:CONFLICT_LOG_PATH}` | Returns the standard path `.github/agents/references/conflict-log.csv` |
| `{MANUAL:FIGURES_DIR}` | Returns `<primary_output_dir>/figures` |
| `{MANUAL:BUILD_OUTPUT_DIR}` | Returns the configured `primary_output_dir` or `dist/` |

Tokens not covered by rule-based fills remain unresolved and are passed to Phase 3.

### Phase 3 — Project Context Fills

If `--project` was provided and the project directory exists, enrichment scans:

- **Jupyter notebooks** in the project tree — detects component-specific tool usage and fills tool reference metadata
- **Import statements** — infers PyPI package names and fetches documentation URLs from the live package registry when available
- **Tool catalog** — builds a catalog of tools detected in project source files, used to supplement sparse `tools[]` metadata in the description

### Phase 4 — AI-Powered Fills (requires `--post-audit`)

For tokens that remain unresolved after Phase 3, the post-audit agent attempts context-aware fills using the project description and manifest as context. AI-assisted completions are reflected as `ai_filled` status in `references/defaults-audit.csv` for review.

---

## `references/defaults-audit.csv`

After enrichment, `references/defaults-audit.csv` contains one row per finding:

| Column | Description |
|---|---|
| `file` | Relative path to the agent file |
| `token` | The unresolved token (e.g. `{MANUAL:STYLE_REFERENCE_PATH}`) |
| `category` | Finding type (`MANUAL_PLACEHOLDER`, `GENERIC_SECTION`, `TOOL_METADATA`, `MISSING_TOOL_REF`) |
| `line_no` | 1-based line number of the finding in the file |
| `section` | Nearest section heading for context |
| `context_snippet` | Nearby file content |
| `auto_suggestion` | Suggested value (when available) |
| `status` | `pending`, `auto_filled`, `ai_filled`, or `skipped` |

Review this file after enrichment to confirm suggestions are appropriate and to identify findings that remain `pending`.

---

## Re-Running Enrichment

Enrichment is idempotent for rule-based fills (the same inputs always produce the same output). It is safe to re-run `--enrich` after editing the project description to pick up new metadata.

For AI-powered fills (`--post-audit`), re-running targets findings that are still marked `pending` in the defaults audit. Already filled findings are not reprocessed.

---

## Security Scan Integration

Run `--scan-security` after enrichment to verify that no filled values introduced security-sensitive content (credentials, absolute paths with usernames, PII):

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --enrich --scan-security
```

The security scan reports:
- PII detection (absolute paths containing usernames)
- Credential patterns (API keys, tokens, passwords)
- Remaining unresolved placeholders
- Artifact validation against JSON schemas

Exit code 1 if issues are found. Use `--auto-correct` to attempt automated repairs (requires `--post-audit`).

---

## CLI Reference Summary

| Flag | Purpose |
|---|---|
| `--enrich` | Run the enrichment pipeline after generation |
| `--post-audit` | Run AI-powered post-generation audit (implies `--enrich`) |
| `--strict-manual-placeholders` | Preserve unresolved MANUAL tokens for optional governance placeholders |
| `--no-strict-manual-placeholders` | Prefer usability defaults (explicit `N/A`) for optional governance placeholders |
| `--scan-security` | Run security scan on output files (use after enrichment) |
| `--auto-correct` | Attempt automated repair of security scan findings (requires `--post-audit`) |
| `--project PATH` | Project directory to scan for context fills (required for Phase 3) |

---

## Best Practices

- **Always run `--enrich` when using `--post-audit`** — the implied enablement is intentional but explicit is clearer in scripts.
- **Review `references/defaults-audit.csv`** after every enrichment run. AI-enriched values are suggestions, not authoritative fills.
- **Run `--scan-security` after enrichment** to catch any fills that accidentally introduced sensitive patterns.
- **Re-enrich after major description changes** — enrichment reads the project description at the time of the run; if you updated tool metadata or component descriptions, run `--enrich` again to pick up the changes.
