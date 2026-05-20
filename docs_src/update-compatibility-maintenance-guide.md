# Update Compatibility Maintenance Guide

## When to Use This Guide

Use this guide if you maintain generated agent infrastructure over time and want to keep teams continuously compatible with `--update` (default merge mode).

This guide focuses on **preventive maintenance**. For one-time migration of legacy unfenced files, use [Migration Guide](migration-guide.md). For run-time update execution steps, use [Update Lifecycle, Drift Detection & Backups](update-lifecycle-guide.md).

Framework output directories differ. Examples in this guide use the default Copilot VS Code path (`.github/agents`); for Copilot CLI and Claude teams, run the same checks against the configured output directory.

---

## Compatibility Contract

For a team to remain compatible with `--update --merge`, all of the following must stay true:

1. Agent files that require selective updates are fence-managed (`AGENTTEAMS:BEGIN/END` markers are present and well-formed).
2. Human customization is placed in USER-EDITABLE regions (outside fenced blocks).
3. Update operations run in merge mode unless full regeneration is intentionally approved.
4. Backup and post-update verification are always performed before commit.

If any condition fails, `--update` may skip files, overwrite user-authored content (in overwrite mode), or leave stale infrastructure in place.

---

## Maintenance Checklist

### 1) Fence Coverage Health

Run a periodic fence-health check against generated agent files:

```bash
find .github/agents -name '*.md' -type f -print0 | \
  xargs -0 -I{} sh -c "grep -q 'AGENTTEAMS:BEGIN' '{}' || echo UNFENCED:{}"
```

If any files are unfenced:

- Prefer targeted retrofit for selected files:

```bash
agentteams --add-fence-markers .github/agents/<file>.md --in-place --yes
```

- Or perform one-step legacy migration for the whole team:

```bash
agentteams --description brief.json --project . --migrate --yes
```

### 2) Safe Update Invocation

Use merge mode for routine maintenance:

```bash
agentteams --description brief.json --project . --update --merge
```

Note: `--update` defaults to merge behavior. `--update --merge` is the same behavior expressed explicitly for script/readability safety.

Use overwrite only for intentional full regeneration with approval:

```bash
agentteams --description brief.json --project . --update --overwrite --yes
```

### 3) Pre-Commit Verification

Before commit, run:

```bash
agentteams --description brief.json --project . --check
```

Expected result after a completed update cycle: no drift detected.

### 4) Outside-Fence Diff Review

After update, inspect diffs and verify no accidental outside-fence deletions:

```bash
git diff -- .github/agents
```

If outside-fence user content changed unexpectedly, restore from backup or git and re-run with corrected scope.

### 5) Backup Hygiene

Keep the `.agentteams-backups/` snapshots until update verification is complete and reviewed. Do not delete latest backup artifacts before validating the run.
After verification and commit, archive or prune backups according to repository retention policy.

---

## Ongoing Governance Rules

- Do not hand-edit template-owned fenced bodies if the same intent belongs in source templates.
- Do not place project-specific policy rows inside fenced generated tables; add them in USER-EDITABLE gaps.
- Keep section manifests accurate in template files when adding/removing fenced sections.
- Treat repeated merge skips (`No fence markers detected`) as infrastructure debt and schedule remediation.
- Pair periodic `--check` with a dry-run update (`--update --dry-run`) in CI for early drift detection.

---

## CI Maintenance Pattern

Recommended minimum CI cadence:

1. Run `--check` on pull requests touching templates, description files, or agent docs.
2. Run scheduled `--update --dry-run` to detect latent compatibility drift.
3. Require review for any overwrite-mode update run.

Example PR check:

```yaml
- name: Agent team compatibility check
  run: |
    agentteams --description brief.json --project . --check
```

---

## Failure Modes and Fast Fixes

### Symptom: `--update --merge` skips many files

Cause: Legacy unfenced files.

Fix: Retrofit fences (`--add-fence-markers`) or run `--migrate`.

### Symptom: User-authored text disappears

Cause: Overwrite-mode run (`--update --overwrite`) replaced full file.

Fix: Restore backup, rerun in merge mode, and keep edits in USER-EDITABLE regions.

### Symptom: Drift keeps returning on every cycle

Cause: Template or description changes are landing without consistent update reconciliation.

Fix: Standardize maintenance cycle: `--check` -> `--update --merge` -> diff review -> commit.
