# Remediation Plan — Warning Signal Calibration + Collector Update (2026-05-21)

## Objective

Reduce non-actionable CH14 warning noise while preserving strict warning signals for real inline-data sprawl, then proceed with collector-management infrastructure update monitoring.

## Approved Remediation Scope

1. Add explicit CH14 allow-marker support in `agentteams/audit.py`.
2. Apply markers to intentional inline checklist/table sections in four templates:
- `templates/universal/agent-updater.template.md`
- `templates/domain/module-doc-validator.template.md`
- `templates/domain/retrieval-integrator.template.md`
- `templates/domain/content-enricher.template.md`
3. Add tests covering marker behavior (ignore marked blocks, still flag unmarked blocks).
4. Re-run self update merge + post-audit to verify warning reduction.
5. Commit/push validated changes.
6. Run collector-management `--update` and monitor for additional module debugging opportunities.

## Non-Goals

1. Do not suppress unresolved manual placeholder warnings.
2. Do not globally relax CH14 thresholds.
3. Do not alter governance semantics for manual completion obligations.

## Risk Controls

1. Marker scope must be narrow and section-local.
2. CH14 must continue to flag unmarked long inline blocks.
3. Keep CI tests as guardrails against accidental broad suppression.
4. If collector update reveals regressions, stop and remediate in module before further rollout.

## Acceptance Criteria

1. Self post-audit no longer reports CH14 warnings for the four intentional sections.
2. Manual placeholder warning remains visible where expected.
3. Tests pass, including new CH14 marker tests.
4. Collector-management update completes with monitored findings captured.
