# Remediation Plan — Orchestrator Update-Guidance Access Hardening (2026-05-21)

## Objective

Improve point-of-action discoverability so orchestrators can reliably coordinate on-the-fly agent-file updates that remain compatible with future `agentteams --update --merge` operations.

## Approved Change Set

1. **Orchestrator template source-pack section**
   - Add an `Update Compatibility Source Pack` section to `agentteams/templates/universal/orchestrator.template.md`.
   - Include concise pointers to canonical guidance documents and a minimal command sequence (`--check`, `--update --merge`, optional security scan, post-audit flow).
   - Keep the section concise and pointer-based to avoid protocol duplication.

2. **Generated-output guardrail test**
   - Update integration assertions to require presence of the new source-pack heading and at least one canonical command cue in generated `orchestrator.agent.md`.
   - This protects against accidental removal in future template edits.

3. **Expected fixture refresh**
   - Refresh affected expected output snapshots under `examples/*/expected/` for files changed by the orchestrator template update.

## Execution Order

1. Update orchestrator template.
2. Update integration test assertions.
3. Regenerate and refresh expected snapshot files impacted by template changes.
4. Run targeted tests, then broader suite as needed.

## Success Criteria

1. Generated orchestrator files include the new source-pack section.
2. Integration tests enforce presence of the section and command cues.
3. Snapshot comparisons pass after fixture refresh.
4. No regression in update/audit behavior.

## Rollback

- Revert template/test/snapshot commits if failures surface.
- Re-run integration snapshots from previous commit to restore baseline.