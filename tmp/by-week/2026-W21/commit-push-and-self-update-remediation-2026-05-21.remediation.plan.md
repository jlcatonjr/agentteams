# Remediation Plan — Self-Update Challenges (2026-05-21)

## Objective

Reduce self-update audit warnings by correcting module-level template defects and reapplying updates through the module update path.

## Approved Actions

1. Update `agentteams/templates/domain/post-production-auditor.template.md`:
   - Add canonical Invariant Core marker line.

2. Update `agentteams/templates/workstream-expert.template.md`:
   - Remove duplicated trailing `Component Brief Preparation`/`Review Protocol`/`Verdict Format` block.

3. Apply fixes through updated module flow:
   - Run `python build_team.py --self --migrate --yes --security-offline`.
   - Run `python build_team.py --self --update --merge --yes --security-offline --post-audit`.

4. Validate:
   - Run targeted tests impacted by template output (`tests/test_integration.py`).
   - Confirm warning reductions and classify residual expected/manual warnings.

## Success Criteria

1. AR spec warning for post-production auditor is cleared.
2. Workstream expert duplication is removed from generated output.
3. Self-update post-audit warnings are reduced and residuals are explainable.
4. Tests pass and changes are push-ready.