# Findings Report — Orchestrator Request Lifecycle Protocol Revision (2026-05-21)

## Scope

- Target protocol surface:
  - `agentteams/templates/universal/orchestrator.template.md`
  - `agentteams/templates/universal/agent-updater.template.md`
- Question: Does current protocol require every incoming orchestrator request to follow the sequence:
  1) domain identification,
  2) investigation report,
  3) adversarial+conflict audit of report,
  4) implementation plan,
  5) adversarial+conflict audit of plan,
  6) end-to-end implementation?

## Findings

1. **Current protocol strongly enforces plan creation and per-step reassessment**, but not a universal per-request investigation/report stage.
   - Constitutional rule and pre-execution sections require plan documentation and step-wise adversarial/conflict reassessment.
   - This satisfies the "after each step" requirement and should be preserved.

2. **Workflows are trigger-specific and begin at execution paths**, not a standardized request-intake gate.
   - Existing workflows (produce/revise/audit/etc.) assume routing has already occurred.
   - There is no explicit mandatory Workflow 0 requiring domain identification + investigation report before all other workflows.

3. **Domain routing exists as a table, but no explicit "identify domain of problem" procedural step for every request.**
   - The routing table is present and useful, but enforcement language for universal request intake sequence is missing.

4. **Conclusion:**
   - The current protocol partially meets requested behavior (plan + per-step audits), but does not fully codify the universal request lifecycle sequence requested by user.

## Recommended Change Direction

1. Add a mandatory request-intake protocol section in `orchestrator.template.md` that every request must pass before workflow-specific execution.
2. Introduce a dedicated `Workflow 0: Request Intake and Problem Framing (Mandatory)` in available workflows.
3. Preserve and reference existing per-step adversarial/conflict reassessment rules.
4. Add integration guardrail assertion so generated orchestrators retain the mandatory lifecycle contract.