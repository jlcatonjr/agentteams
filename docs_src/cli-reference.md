# CLI Reference

All flags for the `agentteams` command (entry point: `build_team.py`).

---

## Synopsis

```
agentteams [--description PATH] [--project PATH] [--framework NAME]
           [--output DIR] [--dry-run] [--overwrite] [--yes]
           [--no-scan] [--update] [--prune] [--check]
           [--scan-security] [--self] [--post-audit] [--auto-correct]
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

### `--version`

Print the version and exit.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error (validation failure, file not found, drift detected with `--check`, security issues with `--scan-security`) |
