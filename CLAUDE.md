# Claude Bridge Entry Point

Use source framework `copilot-vscode` as canonical agent infrastructure.
Read `references/bridges/copilot-vscode-to-claude/agent-inventory.md` and `references/bridges/copilot-vscode-to-claude/quickstart-snippet.md`.
Start with orchestrator routing.

## Mandatory Safety References (read before acting)

- **`references/bridge-refresh-safety.md`** — Pre-Flight checks required before any `agentteams … --bridge-refresh` against an external project. `--bridge-refresh` is destructive at the target; default to `--bridge-merge` whenever target entry files exist without `AGENTTEAMS-BRIDGE` fences. Binds @orchestrator, @git-operations, @security, @cleanup.

## Repository Filing Conventions (read before creating any document)

**Never write a plan, investigation, feasibility report, or change report to the
repository root.** Use the canonical homes:

- **Active operational plans + step CSVs** → `tmp/by-week/YYYY-Www/<slug>.plan.md` (+ `.steps.csv`)
- **Retained local plans / reports / investigations** → `references/plans/<slug>.plan.md` (or `.report.md`)
- **Published reference docs** → `references/`; **user docs** → `docs_src/`; **work summaries** → `workSummaries/`

The repo root holds only canonical project files (README, CHANGELOG, CLAUDE,
SECURITY, STABILITY, LICENSE, `build-team-plan.md`, `bridge-offline-investigation.md`).
Full policy: **`references/filing-conventions.md`**. Enforced by `tests/test_root_doc_hygiene.py`.
