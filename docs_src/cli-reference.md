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
           [--refresh-index] [--query-index TEXT] [--query-k N] [--query-strategy {lexical,vector}]
           [--fail-on-legacy-skip]
           [--scan-security] [--self] [--post-audit] [--auto-correct] [--enrich]
           [--strict-manual-placeholders] [--no-strict-manual-placeholders]
           [--no-backup] [--shrink-policy {warn,halt,allow}]
           [--list-backups] [--restore-backup TIMESTAMP]
           [--target-host-features TOKENS]
           [--capture-baseline PATH] [--baseline-label LABEL] [--check-baseline PATH]
           [--security-offline] [--security-max-items N] [--security-no-nvd]
           [--migrate] [--revert-migration]
           [--fleet DIR] [--fleet-frameworks {github,claude,both}] [--fleet-report DIR]
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

Refresh bridge artifacts by **destructively overwriting** existing bridge outputs **and target-framework entry files** (`CLAUDE.md`, `.claude/agent-team.md`, `.claude/quickstart-snippet.md`, `.claude/README.md`, etc.) at the output root. Use for initial generation or when consumer entry files are known-disposable. For non-destructive refresh, use `--bridge-merge`.

### `--bridge-merge`

Non-destructive bridge update. Regenerates bridge-internal artifacts under `references/bridges/<src>-to-<target>/` (always overwrites those — bridge-owned). For target-framework entry files, only re-renders content inside `<!-- AGENTTEAMS-BRIDGE:BEGIN <region> v=N --> ... <!-- AGENTTEAMS-BRIDGE:END <region> -->` fences. Content outside fences is preserved verbatim. Files lacking any bridge fence are skipped with notices written to `bridge-merge.report.md`. First-time consumers should use `--bridge-refresh`; subsequent refreshes should use `--bridge-merge` to preserve consumer customization.

### `--bridge-no-skills`

Suppress emission of `.claude/skills/recall.md` (Claude target only). The recall skill wraps `agentteams --query-index` for in-session memory-index retrieval; disable when your team manages skills via another channel.

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
- `--bridge-check`, `--bridge-refresh`, and `--bridge-merge` are mutually exclusive; at most one may be passed.
- `--refresh-index` and `--query-index` are mutually exclusive.
- `--query-k` must be `>= 1`.
- `--fleet` requires `--update` and `--merge`, forbids `--shrink-policy=allow`, and is mutually exclusive with `--self`, `--project`, `--description`, `--output`, `--overwrite`, `--prune`, `--migrate`, `--revert-migration`, `--adopt-orphans`, `--bridge-from`, `--bridge-refresh`, `--convert-from`, `--interop-from`, `--refresh-index`, `--query-index`, `--list-backups`, `--restore-backup`, `--add-fence-markers`, `--capture-baseline`, and `--check-baseline` (it operates on many workspaces, each resolved independently).

Excluded with `--convert-from`, `--interop-from`, or `--bridge-from`:
- `--description`, `--project`, `--self`, `--no-scan`, `--update`, `--prune`, `--check`, `--refresh-index`, `--query-index`, `--scan-security`, `--post-audit`, `--auto-correct`, `--enrich`, `--merge`, `--migrate`, `--revert-migration`, `--list-backups`, `--restore-backup`

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

### `--refresh-index`

Rebuild only `references/memory-index.json` in the output directory. This mode does not emit/update agent templates and is intended for fast memory-index refresh after editing source history documents (for example `workSummaries/`, `CHANGELOG.md`, `README.md`, `docs_src/*.md`, or `references/*.md`).

### `--query-index TEXT`

Query an existing `references/memory-index.json` and print ranked hits (title, path, score, snippet). Exits `0` when at least one hit is found and `1` when no matches are found.

### `--query-k N`

Number of ranked results to return with `--query-index`. Default: `5`.

### `--query-strategy {lexical,vector}`

Retrieval strategy for `--query-index`. Default: `lexical`.

- `lexical` — BM25 term-frequency ranking. High precision for keyword/exact-term queries ("when was X decided?", "where is the delivery doc?").
- `vector` — Sparse tf·idf cosine similarity. Better recall for thematic/semantic queries ("what's our policy on error handling?", "find prior work on resource management"). Returns documents related to ALL query terms. Stdlib-only, <100ms at typical corpus sizes.

Start with `lexical`; if results are low-confidence, retry with `vector`.

### `--fail-on-legacy-skip`

Exit with non-zero status if `--merge` skipped any files due to missing fence markers (legacy files). Use in CI to enforce that template updates always propagate to downstream repositories.

Without this flag, legacy skips are reported in the end-of-run summary but the exit code remains `0`. The summary block also fires without the flag — the flag only changes the exit code so CI can fail builds on detected propagation gaps.

**Remediation** for files that appear in the legacy-skip block:

- `agentteams --add-fence-markers <path> [--in-place]` — retrofit AGENTTEAMS fence markers so the next `--merge` run updates the file.
- `agentteams ... --overwrite` — replace unconditionally (will discard any local edits to those files; use only after backup).

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

### `--strict-manual-placeholders`

Preserve unresolved `{MANUAL:*}` placeholders for optional governance fields instead of replacing them with usability defaults.

This mode is the default for `--self` runs.

### `--no-strict-manual-placeholders`

Disable strict manual placeholder preservation and apply usability-oriented defaults for optional governance placeholders:

- `REFERENCE_DB_PATH` -> `N/A - no citation database configured for this project`
- `STYLE_REFERENCE_PATH` -> `N/A - no formal style guide defined for this project` (or the configured `style_reference` value)

This mode is the default for non-self runs.

---

## Backup Options

By default, `--overwrite`, `--merge`, and `--update` all take an automatic backup of the output directory before writing. Backups are stored at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/`.

### `--no-backup`

Skip the automatic backup. The write proceeds without creating a backup.

### `--shrink-policy {warn,halt,allow}`

*(T2.D5)* Controls behaviour when a fenced-region merge would lose
concrete references (paths, identifiers, CVE IDs, list items) from
the on-disk fence body relative to the freshly rendered content.

- `warn` (default, back-compatible): log the shrink notice into the
  emit notices stream and proceed with the smaller content. The
  notice is also appended to
  `tmp/daily-pipeline/shrink-events/<date>.md` (gitignored) with
  the backup directory path so the operator can recover lost
  content.
- `halt`: log the notice, refuse the write, and list the blocked
  file in `EmitResult.shrink_blocked` and on stderr. Returns the
  emit step with the file untouched. Used by the self-team daily
  script (`scripts/run_daily_security_maintenance.sh`) to enforce
  strict fence preservation. Recovery: re-run once with
  `--shrink-policy=allow` (or `warn`), commit the resulting state,
  then halt enforcement resumes on the next cycle.
- `allow`: suppress notices and write the smaller content silently.
  Intended only for that one-time recovery sequence after a
  legitimate upstream-driven shrink (e.g., a retired CVE feed
  entry).

Consumer-repo invocations of `build_team.py` continue to default to
`warn`. The flag is plumbed into both emit code paths (the
`--update` branch and the post-emit main path).

Under `warn`, the full pre-merge body of every shrunken fence is
written to `<backup>/<rel_path>.lost.<sid>.md` (the backup is taken
automatically before the merge) and the shrink Notice is annotated
with `— recovery: <sidecar-path>` so the operator can recover
dropped hand-edits without diffing the whole-file backup. The fence
allowlist `_LIVE_DATA_FENCES` (`threat_intelligence`, `threat_data`)
is exempt — those fences are filled each run from live CISA KEV /
NVD / OSV feeds; CVE rotation is expected.

### `--target-host-features TOKENS`

Comma-separated `<namespace>:<feature>` subselectors that gate
opt-in emission paths. Tokens flow onto the manifest and are
consumed by feature-gated emitters. Default emission is unchanged
when omitted. Recognised tokens:

| Token | Effect |
|---|---|
| `bridge:copilot-vscode-to-claude:subagents` | Per-agent Claude subagent stubs under `<project>/.claude/agents/`. |
| `bridge:copilot-vscode-to-claude:hooks` | `.claude/settings.agentteams.example.json` + `.claude/hook-guard.sh`. |
| `bridge:copilot-vscode-to-claude:cache-split` | Cache-aware `CLAUDE.md` (preamble + boundary + dynamic stanza). |
| `bridge:copilot-vscode-to-claude:schedule` | `.claude/schedules.agentteams.json` for the `/schedule` skill. |
| `bridge:copilot-vscode-to-claude:todo-projection` | `.claude/skills/todo-from-plan.md` skill. |

Unknown tokens are syntactically valid but produce no emission.
See [`host_features`](api-reference/host-features.md) for parser
contract.

### `--capture-baseline PATH`

Capture a deterministic SHA-256 manifest of the output tree and
write it to `PATH` (e.g. `tests/baselines/<team>-<framework>.json`).
Used by regression tests to detect emission drift across phases.
Skips the normal generation pipeline.

### `--baseline-label LABEL`

Label embedded in the captured baseline manifest. Defaults to the
`--framework` value when omitted.

### `--check-baseline PATH`

Compare the current output tree against the baseline at `PATH` and
exit non-zero on any diff. Lists added / removed / changed files to
stderr.

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

## Fleet Update (multi-workspace)

Run `--update --merge` across **every** agent-infrastructure workspace under a parent directory (and its subfolders) in one command. Replaces ad-hoc batch scripts and encodes the fleet-update lessons in [`references/systematic-update-lessons.md`](https://github.com/jlcatonjr/agentteams/blob/main/references/systematic-update-lessons.md).

```bash
agentteams --fleet /path/to/parent --update --merge            # dry-run preview (no writes)
agentteams --fleet /path/to/parent --update --merge --yes       # apply
```

How it works, per discovered workspace:

1. **Discovery** — finds dirs containing `.github/agents/` and/or `.claude/`, pruning `node_modules`, `.git`, `.worktrees`, and `archive`, and never recursing into `.github`/`.claude` internals.
2. **Snapshot (git commit)** — before applying, each git workspace's agent-infra state is committed as `chore(fleet): pre-update snapshot` (or left at `HEAD` when already clean). This is the recoverable rollback point and the diff base. (Non-git workspaces rely on the automatic `.agentteams-backups/` snapshot.)
3. **In-process update** — re-enters the standard update path per target with `--update --merge` (copilot-vscode `.github/agents/`, or a native Claude team's `.claude/agents/`) or `--bridge-merge` (for bridge-consumer `.claude/`). No subprocess is spawned, so a successful merge is never misreported because of an interpreter/exit-code quirk; a failure in one target is isolated and the run continues.
4. **Diff analysis** — after applying, `git diff <snapshot>` is classified by the **authoritative content signals** — shrink Notices and deletions inside `USER-EDITABLE` regions — **not** the process exit code. Per-workspace `.diff` files plus `report.json` and `summary.md` are written under `<DIR>/.agentteams-fleet/<run-id>/`.

Statuses per `(workspace, target)`: `OK` (only fenced/generated regeneration), `REVIEW` (shrink Notice or USER-EDITABLE deletion — inspect the diff), `FAIL` (the merge itself errored), `SKIP` (ambiguous `.claude` with no bridge signal and no descriptor), `WOULD-UPDATE` (dry-run).

**Safety:** fleet mode is non-destructive by construction. It is **merge-only** — `--overwrite`, `--prune`, `--migrate`, `--bridge-refresh`, and `--shrink-policy=allow` are rejected, and `.claude/` is only ever **bridge-merged**, never bridge-refreshed. Descriptor resolution prefers `.agentteams/brief.json` over the thin `_build-description.json` stub.

### `--fleet DIR`

Update every agent-infrastructure workspace under `DIR` and its subfolders. Requires `--update --merge`. Defaults to a dry-run preview; pass `--yes` to apply.

### `--fleet-frameworks {github,claude,both}`

Which infrastructures to update per workspace. Default: `both`.

### `--fleet-report DIR`

Directory for the fleet report. Default: `<DIR>/.agentteams-fleet/<run-id>/`.

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
