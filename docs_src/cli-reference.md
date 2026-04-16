# CLI Reference

All flags for the `agentteams` command (entry point: `build_team.py`).

---

## Synopsis

```
agentteams [--description PATH] [--project PATH] [--framework NAME]
           [--output DIR] [--dry-run] [--overwrite] [--merge] [--yes]
           [--no-scan] [--update] [--prune] [--check]
           [--scan-security] [--self] [--post-audit] [--auto-correct] [--enrich]
           [--security-offline] [--security-max-items N] [--security-no-nvd]
           [--migrate] [--revert-migration]
           [--version]
```

---

## Options

### `--description PATH` / `-d PATH`

Project description file (`.json` or `.md`). Required unless `--self` is used.

### `--project PATH` / `-p PATH`

Existing project directory to scan. Overrides `existing_project_path` in the description file. When set, the directory tree is scanned to supplement missing description fields (README content, tools, structure).

### `--framework NAME` / `-f NAME`

Target agent framework. Choices: `copilot-vscode` (default), `copilot-cli`, `claude`.

| Value | Format | Description |
|-------|--------|-------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | VS Code Copilot agents with full handoff support |
| `copilot-cli` | Plain `.md` | Copilot CLI system prompts; YAML and handoff blocks stripped |
| `claude` | Plain `.md` | Claude Projects; output is `CLAUDE.md`-compatible |

### `--output DIR` / `-o DIR`

Output directory for generated agent files. Default: `<project>/.github/agents/`.

### `--dry-run`

Show what would be generated without writing any files. Useful for previewing output before committing.

### `--overwrite`

Overwrite existing agent files without prompting. Default behavior: prompt for each existing file.

### `--merge`

Update only template-fenced regions in existing agent files, preserving all user-authored content outside fence markers. Skips legacy files (no fence markers) with a warning. Use this instead of `--overwrite` for all routine updates once a team has been migrated with `--migrate`.

### `--yes` / `-y`

Non-interactive mode: answer yes to all prompts automatically.

### `--no-scan`

Disable project directory scanning even when `existing_project_path` or `--project` is set.

### `--update`

Re-render drifted agent files and emit newly added agents without touching unchanged files. Preserves manually filled `{MANUAL:*}` values from existing files. Agents removed from the taxonomy are reported but not deleted (use `--prune` to also remove them).

### `--prune`

Used with `--update`: also delete agent files that are no longer part of the team taxonomy.

### `--check`

Check for template drift and structural changes without writing any files. Exits with code `1` if drift or structural changes are detected, `0` otherwise. Suitable for CI gates.

### `--scan-security`

Scan generated agent files for security issues: PII paths (absolute paths containing usernames), credential patterns (API keys, tokens, passwords), and unresolved `{MANUAL:*}` or `{UPPER_SNAKE_CASE}` placeholders.

### `--self`

Operate on the module's own agent team using `.github/agents/_build-description.json`. Equivalent to running `agentteams` with the module's internal description file.

### `--post-audit`

Run a post-generation audit after emit. Performs static checks (unresolved placeholders, YAML integrity, required-agent coverage) and, if the `copilot` CLI is authenticated, an AI-powered conflict and presupposition review via GitHub Models.

### `--auto-correct`

Used with `--post-audit`: after audit finds issues, invoke the standalone `copilot` CLI in non-interactive mode to repair generated team files, then rerun the audit to confirm.

### `--enrich`

Run AI enrichment after generation. Uses the `copilot` CLI (if available) to review and improve generated agent files. Automatically enabled when `--post-audit` is used.

---

## Security Intelligence Options

These flags control the live vulnerability feed used when rendering security-reference agent files.

### `--security-offline`

Use the cached security vulnerability snapshot only — no network fetch. Useful in CI environments or when working without internet access.

### `--security-max-items N`

Maximum number of current vulnerabilities to include in generated security references. Default: `15`.

### `--security-no-nvd`

Skip NVD CVSS enrichment. Avoids approximately 7 seconds of per-CVE rate-limit sleep. CISA KEV and EPSS data are still fetched.

---

## Legacy Fencing Migration

### `--migrate`

One-step migration for repositories that have legacy (unfenced) agent files. Performs two operations atomically:

1. Creates a git tag `pre-fencing-snapshot` at the current HEAD commit — this is the safety rollback point.
2. Runs `--overwrite --yes` to regenerate all agent files with fenced templates.

After completion, prints a **quality-audit checklist** guiding you to:

- Review `git diff pre-fencing-snapshot HEAD` for lost project-specific content
- Restore any custom rules to the `USER-EDITABLE` zone in `orchestrator.agent.md`
- Commit the migrated files
- Switch to `--merge` for all future updates

Requires `--description`. The project directory must be a git repository.

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project /path/to/project \
  --migrate
```

### `--revert-migration`

Undoes a previous `--migrate` run. Runs `git reset --hard pre-fencing-snapshot` in the project directory and deletes the `pre-fencing-snapshot` tag. All overwritten agent files are restored to their pre-migration state.

Requires the project directory to be a git repository with the `pre-fencing-snapshot` tag present. Use `--project` to specify a different directory than `cwd`.

```bash
agentteams --revert-migration --project /path/to/project
```

> **Note:** `--revert-migration` only resets the working tree and index. If you have already pushed the migrated commit to a remote, a force-push is required. That step is intentionally left to the user.

---

## Other Options

### `--version`

Print the version and exit.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error (validation failure, file not found, drift detected with `--check`, security issues with `--scan-security`) |
