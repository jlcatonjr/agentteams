# Findings Report — Self-Update Challenges (2026-05-21)

## Scope

- Command executed: `python build_team.py --self --update --merge --yes --security-offline --post-audit`
- Goal: identify warnings/errors that can be remediated in `agentteams` module and re-applied via updated module workflow.

## Observed Challenge Categories

1. `PROJECT_NAME_MISMATCH` warnings in multiple workstream expert agent files.
2. `AR_SPEC_VIOLATION` warning for missing Invariant Core marker in `post-production-auditor.agent.md`.
3. `CH14_INLINE_DATA_BLOCK` warnings in `post-production-auditor.agent.md` and `module-doc-expert.agent.md`.

## Root-Cause Assessment

1. **Workstream expert template contains duplicated body blocks.**
   - `agentteams/templates/workstream-expert.template.md` repeats `Component Brief Preparation` and `Review Protocol` sections after the verdict block.
   - This inflates inline-data runs and contributes to CH14/noise in generated expert files.

2. **Post-production-auditor template omits canonical Invariant Core marker line.**
   - `agentteams/templates/domain/post-production-auditor.template.md` has an Invariant Core section but does not include `> ⛔ **Do not modify or omit.**`.
   - This triggers AR spec compliance warnings.

3. **Project-name mismatch warnings are partly legacy-content carryover under merge semantics.**
   - Several generated workstream expert files still contain `CoPilotAgentDocumentation` in unfenced user-preserved regions.
   - `--update --merge` intentionally preserves non-fenced content, so these strings can persist from pre-fence or legacy generations.

## Remediability Decision

1. **Remediate now in module:**
   - Remove duplicate blocks from `workstream-expert.template.md`.
   - Add canonical Invariant Core marker to `post-production-auditor.template.md`.

2. **Mitigate via updated module tooling (best-effort):**
   - Run `--self --migrate --yes` to retrofit fence coverage where missing.
   - Re-run `--self --update --merge --post-audit` to apply corrected templates through fenced updates.

3. **Residual risk note:**
   - Some legacy user-preserved text may still require manual cleanup where merge intentionally protects non-fenced content.