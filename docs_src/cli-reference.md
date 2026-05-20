# CLI Reference

All flags for the `agentteams` command (entry point: `build_team.py`).

---

## Synopsis

```
agentteams [--description PATH] [--project PATH] [--framework NAME]
           [--output DIR] [--convert-from DIR] [--interop-from DIR]
           [--interop-source-framework NAME] [--interop-mode MODE]
           [--bridge-from DIR] [--bridge-source-framework NAME]
           [--bridge-check] [--bridge-refresh]
           [--dry-run] [--overwrite] [--merge] [--yes]
           [--no-scan] [--update] [--prune] [--check]
           [--scan-security] [--self] [--post-audit] [--auto-correct] [--enrich]
           [--no-backup] [--list-backups] [--restore-backup TIMESTAMP]
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
| `copilot-cli` | Plain `.md` | Copilot CLI system prompts; inline YAML and handoff sections stripped, with handoffs preserved in `references/runtime-handoffs.json` when present |
| `claude` | Claude front matter `.md` | Claude Projects; output includes `CLAUDE.md` instructions and preserves handoffs in `references/runtime-handoffs.json` when present |

`references/runtime-handoffs.json` is a framework-neutral sidecar manifest emitted when extracted handoffs exist for frameworks that do not keep inline VS Code handoff syntax in the final agent file.

### `--output DIR` / `-o DIR`

Output directory for generated agent files. Defaults by framework:

- `copilot-vscode`: `<project>/.github/agents/`
- `copilot-cli`: `<project>/.github/copilot/`
- `claude`: `<project>/.claude/agents/`

### `--convert-from DIR`

Convert an existing team from `DIR` into the target `--framework` instead of rendering from a brief.

- Preserves agent body prose.
- Replaces front matter and framework wrappers.
- Converts instructions naming (`copilot-instructions.md` <-> `CLAUDE.md`) based on target.
- Supports all six directional combinations between `copilot-vscode`, `copilot-cli`, and `claude`.
- Non-dry-run conversions run the same live security freshness preflight as the main render path; stale or unavailable security intel blocks writes unless a valid signed waiver exists in `references/security-waivers.log.csv` and `AGENTTEAMS_WAIVER_SIGNING_KEY` is configured.

### `--interop-from DIR`

Run the CAI-based interop pipeline from an existing source team.

- `direct` mode writes target framework files.
- `bundle` mode writes target files and compatibility artifacts under `references/interop/<source>-to-<target>/`.
- Non-dry-run interop runs also enforce the live security freshness preflight before writing, with the same signed-waiver exception path.

### `--interop-source-framework NAME`

Optional source framework override for interop runs. When omitted, source framework is auto-detected.

### `--interop-mode MODE`

Interop mode selector:

- `direct` (default)
- `bundle`

Bundle artifacts:
- `team-manifest.cai.json`
- `interop-manifest.json`
- `routing-map.json`
- `instructions-map.json`
- `compatibility-report.md`

### `--bridge-from DIR`

Generate lightweight target-framework bridge artifacts that reference source canonical agents without regenerating source agent documentation.

Bridge artifacts are written under:

- `references/bridges/<source>-to-<target>/`

Bridge supports all six directional combinations between `copilot-vscode`, `copilot-cli`, and `claude`.
- Bridge generation runs the live security freshness preflight before writing (including signed-waiver exception support); `--bridge-check` remains read-only and only verifies source-file freshness.

### `--bridge-source-framework NAME`

Optional source framework override for bridge mode. If omitted, source framework is auto-detected.

### `--bridge-check`

Validate bridge freshness against source files by comparing source-file checksums with the bridge manifest.

### `--bridge-refresh`

Refresh bridge artifacts by overwriting existing bridge outputs.

---

## How These Three Options Differ

If you are deciding between the interoperability options:

1. `--convert-from` performs direct format migration between framework outputs.
2. `--interop-from` performs Canonical Agent Interface (CAI) normalization and re-emission.
3. `--bridge-from` creates a lightweight runtime bridge that preserves source canonical agent docs.

Choose by intent:

1. Use `--convert-from` for straightforward target-format rewriting.
2. Use `--interop-from` when you need canonical transport and optional compatibility bundle artifacts.
3. Use `--bridge-from` when you need target-runtime access without replacing source documentation.

---

## Bridge Automation Procedures

This repository includes automated bridge upkeep:

1. `.github/workflows/bridge-maintenance.yml` (daily + manual) runs `scripts/run_daily_bridge_maintenance.sh`.
2. The script runs security maintenance first, then performs `--bridge-refresh` and `--bridge-check` for maintained pairs, and writes run summaries to `tmp/bridge-maintenance/`.
3. `.github/workflows/security-maintenance.yml` is retained as a manual fallback (`workflow_dispatch`) for incident response or ad-hoc reruns.
4. `.github/workflows/bridge-watchdog.yml` opens a deduplicated issue if the latest successful bridge maintenance run is stale.

For mode comparison and architecture-level guidance, see [Interoperability](interoperability.md).

---

## Explicitly Excluded Option Pairs

The CLI rejects incompatible pairs explicitly.

Global exclusions:
- `--convert-from` and `--interop-from` cannot be used together.
- `--bridge-from` cannot be used with `--convert-from` or `--interop-from`.
- `--auto-correct` requires `--post-audit`.
- `--prune` requires `--update`.
- `--bridge-check` cannot be combined with `--bridge-refresh`.

Excluded with `--convert-from`, `--interop-from`, or `--bridge-from`:
- `--description`, `--project`, `--self`, `--no-scan`, `--update`, `--prune`, `--check`, `--scan-security`, `--post-audit`, `--auto-correct`, `--enrich`, `--merge`, `--migrate`, `--revert-migration`, `--list-backups`, `--restore-backup`

### `--dry-run`

Show what would be generated without writing any files. Useful for previewing output before committing.

### `--overwrite`

Overwrite existing agent files without prompting. Default behavior: prompt for each existing file.

### `--merge`

Update only template-fenced regions in existing agent files, preserving all user-authored content outside fence markers. Skips legacy files (no fence markers) with a warning. This is the default behavior for `--update`; pass `--merge` explicitly if you want to make this intent clear in scripts or CI. Use `--overwrite` only when intentional full-file regeneration is needed (requires security clearance).

### `--yes` / `-y`

Non-interactive mode: answer yes to all prompts automatically.

### `--no-scan`

Disable project directory scanning even when `existing_project_path` or `--project` is set.

### `--update`

Re-render drifted agent files and emit newly added agents without touching unchanged files. Preserves manually filled `{MANUAL:*}` values from existing files. Agents removed from the taxonomy are reported but not deleted (use `--prune` to also remove them).

A backup of the output directory is created automatically before any writes. By default, `--update` uses merge mode (equivalent to `--update --merge`): only template-fenced regions are re-rendered, and user-authored content outside fence markers is preserved. To perform a full destructive re-render, pass `--update --overwrite` (this invokes the security gate and requires a clearance in `references/security-decisions.log.csv`). Use `--no-backup` to suppress the backup.

On a successful (non-dry-run) `--update`, AgentTeams writes a delivery receipt to `references/delivery-receipt.json` (schema: `schemas/delivery-receipt.schema.json`) recording the project name, framework, manifest fingerprint, and fingerprint algorithm version of the delivered build. When no material drift is detected but the build-log baseline is stale (for example after a `FINGERPRINT_ALGO_VERSION` bump), the baseline is healed in place: the build-log is rewritten first, the delivery receipt is then written against the healed baseline (heal-first-attest-second), and `--update` prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).` after both writes complete. Receipt write failures warn on stderr but do not fail the run.

### `--prune`

Used with `--update`: also delete agent files that are no longer part of the team taxonomy.

### `--check`

Check for template drift and structural changes without writing any files. Exits with code `1` if drift or structural changes are detected, `0` otherwise. Suitable for CI gates.

When the structural diff reports a manifest-promotion event (manifest fingerprint changed, fingerprint unavailable, or `fingerprint_algo_version` bumped), `--check` runs the full render pipeline in memory and reconciles each promoted file against its on-disk content; fingerprint-only promotions whose rendered output matches disk byte-for-byte are demoted back to unchanged. `--check` and `--update --dry-run` report the same `has_changes` set for the same inputs.

### `--scan-security`

Scan generated agent files for security issues: PII paths (absolute paths containing usernames), credential patterns (API keys, tokens, passwords), and unresolved `{MANUAL:*}` or `{UPPER_SNAKE_CASE}` placeholders.

### `--self`

Operate on the module's own agent team using `.github/agents/_build-description.json`. Equivalent to running `agentteams` with the module's internal description file.

### `--post-audit`

Run a post-generation audit after emit. Performs static checks (unresolved placeholders, YAML integrity, required-agent coverage) and, if the `copilot` CLI is authenticated, an AI-powered conflict and presupposition review via GitHub Models.

### `--auto-correct`

Used with `--post-audit`: after audit finds issues, invoke the standalone `copilot` CLI in non-interactive mode to repair generated team files, then rerun the audit to confirm.

### `--enrich`

After generating the team, scan for default template elements (unresolved `{MANUAL:*}` placeholders, underdeveloped sections, incomplete tool metadata) and attempt context-aware auto-enrichment. Exports a `defaults-audit.csv` to the `references/` directory. Combine with `--post-audit` to also run AI-powered enrichment.

---

## Backup Options

By default, `--overwrite`, `--merge`, and `--update` all take an automatic backup of the output directory before writing. Backups are stored at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/`.

### `--no-backup`

Skip the automatic backup. The write proceeds without creating a backup.

### `--list-backups`

List all available backups for the output directory (newest first) and exit. Prints timestamp, path, and file count for each backup.

### `--restore-backup TIMESTAMP`

Restore a specific backup into the output directory. `TIMESTAMP` is the directory name shown by `--list-backups` (e.g. `20250601-143022`). Use `latest` to restore the most recent backup.

---

## Security Intelligence Options

These flags control the live vulnerability feed used when rendering security-reference agent files.

### `--security-offline`

Use the cached security vulnerability snapshot only — no network fetch. Useful in CI environments or when working without internet access.

If live security data cannot be fetched and there is no cache to fall back to, the security snapshot is marked stale and write-capable commands block until the feed is refreshed. A valid signed waiver in `references/security-waivers.log.csv` can authorize a bounded exception when `AGENTTEAMS_WAIVER_SIGNING_KEY` is configured.

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
