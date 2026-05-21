# Implementation Plan — Orchestrator Request Lifecycle Protocol Revision (2026-05-21)

## Objective

Codify a mandatory per-request lifecycle in the orchestrator protocol while preserving existing step-level adversarial/conflict reassessment.

## Approved Changes

1. **Orchestrator template protocol update**
   - Add a mandatory `Request Intake and Problem Framing Protocol` section in `agentteams/templates/universal/orchestrator.template.md`.
   - Explicitly require this 6-stage sequence for every request:
     1) identify domain,
     2) investigate and produce report,
     3) adversarial + conflict audit of report,
     4) prepare implementation plan,
     5) adversarial + conflict audit of plan,
     6) implement end-to-end.

2. **Workflow system update**
   - Add `Workflow 0: Request Intake and Problem Framing (Mandatory)` in available workflows.
   - Ensure all existing workflow references to Final Check remain consistent after insertion.

3. **Guardrail test update**
   - Extend `tests/test_integration.py` with semantic assertions that generated orchestrators include:
     - mandatory intake lifecycle section/wording,
     - explicit report audit and plan audit cues.

4. **Snapshot refresh**
   - Refresh expected orchestrator snapshots for examples affected by template changes.

## Validation Plan

1. Run `pytest tests/test_integration.py -q --tb=short -x`.
2. Confirm snapshot comparison passes for software/research/data-pipeline.

## Success Criteria

1. Generated orchestrator text enforces the required 6-stage lifecycle.
2. Existing per-step adversarial/conflict reassessment language remains present.
3. Tests and snapshots are green.