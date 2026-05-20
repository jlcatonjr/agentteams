# Update Lifecycle, Drift Detection & Backups

## When to Use This Guide

Read this guide if you:

- Need to bring an existing agent team up to date after template changes
- Want to understand what `--update --merge` does step-by-step before running it on a production team
- Have diverged agent content and need to reconcile manual edits with a fresh template render
- Want to understand when and how backups are created and how to restore from them
- Use `--check` to test freshness without committing to a write

---

## Core Concepts

### Two Kinds of Drift

| Drift Type | Cause | Detected By |
|---|---|---|
| **Content drift** | Template changed; rendered output now differs from what the file contains | `--update --dry-run` or `--check` |
| **Structural drift** | Planned output files or template-to-output mappings changed since the previous build | `--check` (drift + structural diff report) |

Content drift is normal and expected over time. `--migrate` is only for legacy pre-fencing teams that need a one-time overwrite migration.

### Manifest Fingerprint, Baseline Heal, and Delivery Receipt

Each build records a manifest fingerprint and a `fingerprint_algo_version` in `build-log.json`. When `--check` detects a manifest-promotion event (fingerprint changed, unavailable, or algo version bumped) it renders the affected files in memory and demotes any whose rendered content already matches disk — so `--check` and `--update --dry-run` report the same `has_changes` set. When `--update` finds no material drift but the recorded baseline is stale (typical after a `FINGERPRINT_ALGO_VERSION` bump), the build-log baseline is healed in place (rewritten with the current fingerprint and algo version). Every successful non-dry-run `--update` then writes `references/delivery-receipt.json` (schema: `schemas/delivery-receipt.schema.json`) attesting the delivered fingerprint and algo version; the receipt is excluded from drift detection. After the build-log and receipt are written, the run prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).` so the convergence is observable. See [Delivery Procedure](delivery-procedure.md) for the full heal-then-attest contract.

### The Three Update Modes

| Mode | Command | Writes Files? |
|---|---|---|
| Check only | `--check` | No |
| Merge (default) | `--update` | Yes — inside fenced sections only |
| Merge (explicit) | `--update --merge` | Yes — inside fenced sections only |
| Full regeneration | `--update --overwrite` | Yes — entire file (requires security clearance) |

---

## Update Deployment Protocol

For production teams, follow this sequence to ensure safe updates:

### Step 1 — Dry Run

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --update --dry-run
```

Inspect the diff output. Verify that:
- Only `FENCED` sections changed
- No USER-EDITABLE content was touched
- No unexpected placeholder regressions appear

### Step 2 — Backup

Backups are created automatically before every write unless `--no-backup` is set. They are stored as timestamped copies alongside the output files. You can list available backups:

```bash
agentteams --description brief.json --list-backups
```

### Step 3 — Run the Merge

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --update
```

### Step 4 — Post-Diff Review

After writing, re-examine the changed files. The merge operation reports section replacements/additions per file. Manually verify that changes occurred only in intended fenced regions and newly appended fenced sections.

### Step 5 — Outside-Fence Analysis

Content outside fenced regions is preserved by merge. However, major template revisions may add or retire fenced sections. Use `git diff` to manually confirm no unintended outside-fence edits were introduced.

### Step 6 — Commit

After reviewing diffs and resolving advisories, commit the updated files:

```bash
git add .github/agents/
git commit -m "chore: update agent team to latest templates"
```

---

## Drift Detection (`--check`)

The `--check` flag validates freshness without writing any files. Use it in CI or pre-merge hooks to detect stale agent content:

```bash
agentteams --description brief.json --project /path --framework copilot-vscode --check
```

Representative output (actual entries vary by team):

```
Changed templates (2):
  templates/orchestrator.template.md -> orchestrator.agent.md

Unchanged: 37 template(s)

Structural changes:
  Added files: 1
  Drifted files: 2
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | All files are fresh |
| `1` | One or more files are stale (CI should block merge) |

### Using `--check` in CI

```yaml
- name: Check agent team freshness
  run: |
    agentteams \
      --description brief.json \
      --project . \
      --framework copilot-vscode \
      --check
```

This step fails the pipeline if any team files have fallen behind their templates, prompting a developer to run the merge locally before merging.

---

## Backups

### What Is Backed Up

Before any write that modifies existing files, AgentTeams creates a backup snapshot in the output directory under `.agentteams-backups/`:

```
.github/agents/.agentteams-backups/20260511-143200/orchestrator.agent.md
```

Backups are created for both `--merge` and `--overwrite` writes. They are not created for `--dry-run`.

### Listing Backups

```bash
agentteams --description brief.json --list-backups
```

Lists all backup files in the output directory, ordered by timestamp, with the source file they correspond to.

### Restoring a Backup

```bash
agentteams --description brief.json --restore-backup 20260511-143200
```

Restores all backup files from the specified timestamp to their original paths, overwriting current content. Use the exact label shown by `--list-backups` (or `latest`). Restore can also remove files not present in the selected snapshot so the output directory matches that backup state.

### Disabling Backups

```bash
agentteams --description brief.json --update --merge --no-backup
```

Use `--no-backup` only in automated pipelines where you have external version control as your safety net (e.g., git-managed source with a clean working tree before each run).

---

## Prune

Over time, team updates may retire agents that are no longer needed (for example, a component that was removed from the project description). The `--prune` flag removes output files that no longer correspond to any template:

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --update --prune
```

Use `--dry-run` first to review what would be pruned, and ensure version-control rollback is available before applying deletes.

---

## CLI Reference Summary

| Flag | Purpose |
|---|---|
| `--update` | Fetch latest template renders before writing |
| `--merge` | Write only inside fenced sections; preserve everything outside |
| `--overwrite` | Replace entire output file (use when full regeneration is needed) |
| `--dry-run` | Show what would change without writing |
| `--check` | Read-only freshness check; exit 1 if any file is stale |
| `--no-backup` | Skip backup creation |
| `--list-backups` | List available backups for the output directory |
| `--restore-backup TIMESTAMP` | Restore all backups from a given timestamp |
| `--prune` | Remove output files for agents no longer in the manifest |

---

## Best Practices

- **Run `--check` in CI** before merging any branch that changes `brief.json` or template library versions.
- **Always `--dry-run` before `--merge`** when updating a team that has significant hand-authored extensions in USER-EDITABLE regions.
- **Use `--no-backup` only in clean-tree pipelines** where you have a guaranteed rollback path via version control.
- **Prune intentionally.** Run `--prune --dry-run` first to inspect the removal list before committing to deletes.

---

## Troubleshooting

### Content I authored outside a fenced region disappeared

**Cause:** The file was updated with `--overwrite` (full regeneration) rather than `--merge`. Full overwrite replaces the entire file.

**Fix:** Restore from backup with `--restore-backup TIMESTAMP`, or from git. Going forward, use `--merge` for iterative updates.

### `--check` reports STALE but `--merge` doesn't change anything

**Cause:** The staleness flag was triggered by a change in template metadata (version attribute, comment text) rather than rendered content. The merge's content diff came up empty.

**Fix:** This is informational. Review the specific section and confirm the template change is benign. If your policy requires zero staleness warnings, a full `--overwrite` cycle clears the flag.

### Restore failed: "no backup found for timestamp"

**Cause:** The timestamp provided doesn't match any backup on disk, or backups were skipped (`--no-backup`).

**Fix:** Run `--list-backups` to see available timestamps. If no backups exist, recover from git.
