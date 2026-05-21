# Findings Report — Post-Audit Warnings Analysis (2026-05-21)

## Scope

- Source log: `/tmp/agentteams-self-update-merge-postaudit-afterfix4.log`
- Target: self-maintenance update path and generated agent infrastructure quality signals

## Warning Set Observed

1. `UNRESOLVED_MANUAL_PLACEHOLDER` in `orchestrator.agent.md` for `{MANUAL:STYLE_REFERENCE_PATH}`
2. `CH14_INLINE_DATA_BLOCK` warnings in:
- `agent-updater.agent.md`
- `module-doc-validator.agent.md`
- `retrieval-integrator.agent.md`
- `content-enricher.agent.md`

## Root-Cause Analysis

### 1) Manual Placeholder Warning

Cause:
- `{MANUAL:STYLE_REFERENCE_PATH}` is intentionally unresolved in generated orchestrator docs until a human supplies project-specific style-reference path/value.

Assessment:
- Expected governance behavior; not a module defect.
- Should remain visible as a manual action signal.

### 2) CH14 Inline Data Warnings

Cause:
- CH14 checker flags >10 consecutive table/list lines outside Invariant Core.
- The flagged files contain long, intentional operational checklists and token-reference tables that are part of stable agent instructions.

Assessment:
- Mixed signal quality: warning is structurally correct but operationally noisy for intentionally inlined policy/checklist sections.
- This is a module-level audit-rule calibration opportunity.

## Remediation Decision

1. No remediation for unresolved manual placeholder warning.
- Keep as expected warning (manual completion required).

2. Remediation required for CH14 false-positive/noise pattern.
- Introduce explicit opt-in allow markers for intentional inline data sections.
- Apply markers only to known intentional sections in affected templates.
- Preserve CH14 detection everywhere else.

## Proposed Remediation Direction

1. Extend CH14 checker in `agentteams/audit.py` to ignore inline-data runs between:
- `<!-- CH14:ALLOW_INLINE_DATA -->`
- `<!-- /CH14:ALLOW_INLINE_DATA -->`

2. Add markers around intentional list/table blocks in these templates:
- `agentteams/templates/universal/agent-updater.template.md`
- `agentteams/templates/domain/module-doc-validator.template.md`
- `agentteams/templates/domain/retrieval-integrator.template.md`
- `agentteams/templates/domain/content-enricher.template.md`

3. Add tests proving:
- CH14 still flags non-marked long runs.
- CH14 ignores marked intentional sections.

## Expected Outcome

- Manual placeholder warning remains (by design).
- CH14 warnings drop for the four intentional sections without weakening global CH14 coverage.
