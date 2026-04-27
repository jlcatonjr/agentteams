# Plan: github-merge-doc-infrastructure-2026-04-27

- Trigger: Generalized GitHub documentation was requested to support safe repository interaction and careful merge strategies.
- Goal: Integrate GitHub merge/PR/protection guidance into AgentTeams module infrastructure, generated governance agents, and validation workflows.
- Agent sequence: orchestrator -> security -> adversarial -> conflict-auditor -> primary-producer -> technical-validator -> test-suite-expert -> code-hygiene -> conflict-auditor
- Implementation commit(s):
  - `9042d7f` — source integration, templates, docs alignment, and regression tests
- Success criteria:
  - Canonical GitHub workflows reference is part of generated infrastructure
  - Governance includes `git-operations` in module planning/classification
  - Orchestrator and updater templates route and evaluate merge/documentation impacts
  - Official docs.github.com references are embedded for auditable policy claims
  - Tests pass for changed surfaces
- Rollback notes:
  - Revert with `git revert 9042d7f` (and later commits listed in the steps log)
