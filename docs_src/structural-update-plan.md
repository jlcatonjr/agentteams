# Structural Update Plan — Programmatic Integration of Module Updates to Existing Teams

## Problem Statement

When the agentteams module evolves (e.g., adding `@code-hygiene` as a governance agent), existing teams in downstream projects are left behind. The current `--update` flag only handles **template content drift** — it re-renders files whose `.template.md` source has changed. It does **not** handle:

| Gap | Example |
|-----|---------|
| **New agents** added to the governance tier | `code-hygiene` added to `GOVERNANCE_AGENTS` |
| **New companion files** added to the pipeline | `references/code-hygiene-rules.reference.md` |
| **Removed agents** deprecated from the taxonomy | A governance agent retired in a future version |
| **Team membership changes** in framework instructions (`.github/copilot-instructions.md` or `.claude/CLAUDE.md`) | New agent listings, new constitutional rules |
| **Structural data missing from build-log** | No `output_files_map` or `agent_slug_list` recorded |

Today, the only way to integrate structural changes is full regeneration (`--overwrite`), which discards manually-filled `{MANUAL:*}` values and requires re-resolution.

## Design Goal

Extend `--update` so that it handles both **content drift** (template text changed) and **structural drift** (team composition changed) in a single, non-destructive pass that preserves manual placeholder values.

---

## Implementation Plan

### Phase 1: Enrich build-log.json → schema v1.2

**File:** `build_team.py` → `_write_run_log()`

Add two new fields to the build-log:

```json
{
  "schema_version": "1.2",
  "output_files_map": [
    {
      "path": "code-hygiene.agent.md",
      "template": "universal/code-hygiene.template.md",
      "type": "agent",
      "component_slug": null
    }
  ],
  "agent_slug_list": ["orchestrator", "navigator", "security", "code-hygiene", ...],
  "governance_agents": ["navigator", "security", "code-hygiene", ...],
  "selected_archetypes": ["primary-producer", "quality-auditor", ...]
}
```

- `output_files_map`: The complete `manifest["output_files"]` list. This is the structural fingerprint — it records exactly which files were planned and from which templates.
- `agent_slug_list`: Complete ordered list of all agents in the team.
- `governance_agents`: Snapshot of `GOVERNANCE_AGENTS` at build time.
- `selected_archetypes`: Already present, but verify it's populated.

**Backward compat:** `detect_drift()` already handles missing `template_hashes` (schema v1.0). The structural diff functions will treat missing `output_files_map` as "unknown previous state → treat all new-manifest files as potentially new."

**Effort:** Small — 4 fields added to `_write_run_log()`.

---

### Phase 2: Implement structural diff in `agentteams/drift.py`

**New function:** `compute_structural_diff(old_log, new_manifest, templates_dir) → StructuralDiffReport`

```python
@dataclass
class StructuralDiffReport:
    added_files: list[dict]       # In new manifest, not in old log
    removed_files: list[dict]     # In old log, not in new manifest
    drifted_files: list[dict]     # In both, but template hash changed
    unchanged_files: list[dict]   # In both, same template hash
    content_drift: DriftReport    # The existing template-content drift report
```

**Algorithm:**

1. Load `output_files_map` from the old build-log (or `[]` if missing).
2. Compute `output_files` from the new manifest.
3. Build two dicts keyed by `path`:
   - `old_files = {f["path"]: f for f in old_log["output_files_map"]}`
   - `new_files = {f["path"]: f for f in new_manifest["output_files"]}`
4. Classify:
   - `added = [f for path, f in new_files.items() if path not in old_files]`
   - `removed = [f for path, f in old_files.items() if path not in new_files]`
   - `common = {path for path in new_files if path in old_files}`
   - For each common path: compare template hashes → drifted or unchanged
5. Always mark the framework instructions file as drifted if `agent_slug_list` changed (team membership change forces re-render even if template hash is the same).

**Effort:** Medium — new dataclass + ~60-line function in `drift.py`.

---

### Phase 3: Extend `--update` to handle structural changes

**File:** `build_team.py` → the `if args.update:` block

Replace the current drift-only filter with:

```
1. Compute new manifest (already done — step 3)
2. Load old build-log from output_dir
3. Compute structural diff: drift.compute_structural_diff(old_log, manifest, TEMPLATES_DIR)
4. Build the update set:
   a. ADDITIONS:  new files → render and emit (no MANUAL preservation needed — they're new)
   b. DRIFTED:    changed files → render, preserve MANUAL values from existing, emit
   c. REMOVED:    report to user (do NOT delete without --prune flag)
   d. UNCHANGED:  skip
5. Re-render the framework instructions file if team membership changed
6. Emit the update set
7. Write updated build-log (schema v1.2)
```

**MANUAL preservation** (existing logic preserved):
- For drifted files: scan existing file for resolved `{MANUAL:*}` values; apply to new content.
- For added files: no existing file → MANUAL tokens remain unresolved → appear in SETUP-REQUIRED.md.

**Console output:**

```
Structural update for 'MusicMaker':
  Added:    2 file(s) — code-hygiene.agent.md, references/code-hygiene-rules.reference.md
  Updated:  3 file(s) — orchestrator.agent.md, .github/copilot-instructions.md, ...
  Removed:  0 file(s)
  Unchanged: 35 file(s)
  MANUAL values preserved: 16

  ⚠  2 new placeholder(s) require manual completion.
     Review SETUP-REQUIRED.md in the output directory.
```

**Effort:** Medium — restructure the `--update` block, add structural diff integration.

---

### Phase 4: Add `--prune` flag

**Trigger:** `--update --prune`

When structural diff identifies removed files (agents deprecated from the taxonomy), `--prune` will delete them from the output directory after confirmation.

**Safety:**
- Without `--prune`: removed files are only reported, never deleted.
- With `--prune` but without `--yes`: interactive confirmation listing each file.
- With `--prune --yes`: non-interactive deletion.

**Effort:** Small — new argparse flag + conditional deletion loop in the `--update` block.

---

### Phase 5: Handle edge cases

| Edge Case | Handling |
|-----------|----------|
| **No build-log exists** (team generated by older version) | Treat as "unknown old state" — full re-render with MANUAL preservation from all existing files. Equivalent to `--overwrite` but with MANUAL preservation. |
| **Schema v1.1 build-log** (no `output_files_map`) | Fall back to `files_written` list for removed-file detection; treat all new-manifest files not in `files_written` as potentially new. |
| **Renamed templates** | Detected as one removal + one addition. Acceptable — the new file will be emitted and the old one reported as orphaned. |
| **Component added/removed** | Workstream expert agents follow the same add/remove logic. New components → new expert files. Removed components → reported as removed. |
| **MANUAL value in a removed file** | Log a warning: "Removed file X contained resolved MANUAL values — review before deleting." |

**Effort:** Small — guard clauses and warning messages.

---

### Phase 6: Tests

| Test | File | What it verifies |
|------|------|------------------|
| `test_structural_diff_additions` | `tests/test_drift.py` | New agents in manifest → classified as added |
| `test_structural_diff_removals` | `tests/test_drift.py` | Old log files not in new manifest → classified as removed |
| `test_structural_diff_drifted` | `tests/test_drift.py` | Same path, different hash → classified as drifted |
| `test_structural_diff_unchanged` | `tests/test_drift.py` | Same path, same hash → classified as unchanged |
| `test_structural_diff_no_old_map` | `tests/test_drift.py` | Missing `output_files_map` → graceful fallback |
| `test_structural_diff_team_membership_change` | `tests/test_drift.py` | Different `agent_slug_list` → copilot-instructions forced drifted |
| `test_update_preserves_manual_values` | `tests/test_integration.py` | `--update` carries forward resolved MANUAL values |
| `test_update_adds_new_agents` | `tests/test_integration.py` | `--update` emits files for new governance agents |
| `test_update_reports_removed` | `tests/test_integration.py` | `--update` reports but does not delete removed files |
| `test_update_prune_deletes` | `tests/test_integration.py` | `--update --prune` deletes removed files |
| `test_build_log_schema_v12` | `tests/test_emit.py` | Build-log includes `output_files_map` and `agent_slug_list` |

**Effort:** Medium — ~11 new test functions.

---

### Phase 7: Documentation

- Update `README.md` → document `--update` structural capabilities
- Update `build-team-plan.md` → add the structural update feature to the architecture
- Update `build-team-steps.csv` → add implementation steps

---

## Execution Order

| Step | Phase | Dependency | Estimated Complexity |
|------|-------|------------|---------------------|
| 1 | Phase 1 — Enrich build-log | None | Small |
| 2 | Phase 2 — Structural diff | Phase 1 | Medium |
| 3 | Phase 6a — Drift tests | Phase 2 | Small |
| 4 | Phase 3 — Extend --update | Phase 2 | Medium |
| 5 | Phase 4 — --prune flag | Phase 3 | Small |
| 6 | Phase 5 — Edge cases | Phase 3 | Small |
| 7 | Phase 6b — Integration tests | Phase 3–5 | Medium |
| 8 | Phase 7 — Documentation | Phase 3 | Small |

## Files Modified

| File | Changes |
|------|---------|
| `build_team.py` | Enrich `_write_run_log()` (Phase 1), restructure `--update` block (Phase 3), add `--prune` flag (Phase 4) |
| `agentteams/drift.py` | Add `StructuralDiffReport`, `compute_structural_diff()`, `print_structural_diff_report()` (Phase 2) |
| `tests/test_drift.py` | New file — structural diff unit tests (Phase 6) |
| `tests/test_integration.py` | Add `--update` integration tests (Phase 6) |
| `README.md` | Document structural update (Phase 7) |
| `build-team-plan.md` | Architecture update (Phase 7) |

## Files NOT Modified

| File | Reason |
|------|--------|
| `agentteams/analyze.py` | Manifest already contains all structural data; no changes needed |
| `agentteams/render.py` | Rendering logic is unchanged; structural diff is a selection problem, not a rendering problem |
| `agentteams/emit.py` | Emit logic is unchanged; the `--update` block in `build_team.py` controls what gets emitted |
| `agentteams/ingest.py` | Input processing is unchanged |
| Templates | Templates are unchanged; this plan is about the update mechanism, not template content |
