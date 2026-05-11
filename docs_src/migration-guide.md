# Migration Guide

## When to Use This Guide

Read this guide if you:

- Have an agent team that was generated before section fencing was introduced and want to bring it under merge-safe management
- Want to use `--update --merge` but your team's files have no fence markers yet
- Need to roll back a migration that produced unexpected results

---

## What Migration Does

Before section fencing existed, AgentTeams generated agent files as flat Markdown with no structure markers. `--update --merge` depends on fence markers to know which regions to replace. Running merge on a pre-fencing file would fail gracefully (no markers to match) but leave the file stale.

The `--migrate` flag performs a one-time legacy migration:

1. Verifies the project is a git repository
2. Creates a `pre-fencing-snapshot` git tag at current `HEAD`
3. Re-runs generation in overwrite mode (`--overwrite --yes`) so agent files are fully regenerated with fenced templates
4. Prints a post-migration checklist for restoring project-specific rules in USER-EDITABLE regions

After migration, `--update --merge` can manage the file normally.

---

## Pre-Migration Checklist

Before running `--migrate`:

- [ ] Working tree is clean (`git status` shows no uncommitted changes)
- [ ] You have run `--dry-run` to review the snapshot-tag + overwrite migration behavior
- [ ] You understand which regions are USER-EDITABLE (they are preserved by future `--merge` updates, but `--migrate` itself performs full overwrite regeneration)
- [ ] You have a backup or recent commit to recover from if needed

---

## Running a Migration

### Step 1 — Dry Run First

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --migrate --dry-run
```

Review the output for:

```
Snapshot tag 'pre-fencing-snapshot' created at HEAD.
Running --overwrite migration...
```

Note: `--migrate --dry-run` still creates the `pre-fencing-snapshot` tag before delegating. If you were only previewing, delete the tag manually (`git tag -d pre-fencing-snapshot`) before a real migrate run.

### Step 2 — Run Migration

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --migrate
```

`--migrate` is full regeneration, not selective insertion. Existing generated files are overwritten with current fenced template output.

Backup files are created before modification.

### Step 3 — Review and Commit

```bash
git diff .github/agents/
git add .github/agents/
git commit -m "chore: migrate agent team to fence-compatible format"
```

The `pre-fencing-snapshot` tag is created automatically by `--migrate` and is used for rollback.

---

## After Migration

Once migration is complete, use `--update --merge` for all subsequent updates. You do not need to run `--migrate` again for already-migrated files.

If you add new templates to the team later, any new files written by a post-fencing generation are fence-compatible from creation. Only files generated before fencing existed need migration.

---

## Rolling Back: `--revert-migration`

If migration produced unexpected results — for example, section content was inserted in the wrong position or template content conflicts with existing prose — roll back:

```bash
agentteams \
  --description brief.json \
  --project /path/to/project \
  --framework copilot-vscode \
  --revert-migration
```

`--revert-migration`:

1. Runs `git reset --hard pre-fencing-snapshot` in the project repository
2. Deletes the `pre-fencing-snapshot` tag
3. Leaves the working tree at the pre-migration commit state

Equivalent manual fallback:

```bash
git reset --hard pre-fencing-snapshot
git tag -d pre-fencing-snapshot
```

---

## Edge Cases

### Mixed team: some files pre-fencing, some post-fencing

`--migrate` still performs overwrite regeneration. If your team is mixed, prefer a dry run and diff review before applying.

### Dirty working tree

`--migrate` warns when uncommitted changes exist, but it does not block execution. Commit first if you need deterministic rollback and clean migration diffs.

### Template updated between generation and migration

If the template has been updated since your files were generated, `--migrate` inserts content from the **current** template version. After migration, run `--update --merge` immediately to confirm the remaining non-fenced regions are still aligned.

---

## CLI Reference Summary

| Flag | Purpose |
|---|---|
| `--migrate` | Add fence markers and initial template content to pre-fencing files |
| `--revert-migration` | Restore files by `git reset --hard pre-fencing-snapshot` and then delete that tag |
| `--dry-run` | Preview migration changes without writing |
| `--no-backup` | Skip backup creation during migration (not recommended) |

---

## Best Practices

- **Always commit before migrating.** The pre-fencing state should be preserved in git history, not just on disk.
- **Do not pre-create `pre-fencing-snapshot`.** `--migrate` creates that tag automatically and aborts if it already exists.
- **Migrate and merge in the same sitting.** Migrating and then leaving files in limbo (without running a subsequent `--update --merge`) creates a window where the file has markers but may have stale content inside them.
- **Review each MIGRATE/SKIP line** in the dry run output. A section marked SKIP (USER-EDITABLE) will not receive template content — confirm that's correct for each skip.
