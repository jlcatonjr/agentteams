# Claude Bridge Entry Point

Use source framework `copilot-vscode` as canonical agent infrastructure.
Read `references/bridges/copilot-vscode-to-claude/agent-inventory.md` and `references/bridges/copilot-vscode-to-claude/quickstart-snippet.md`.
Start with orchestrator routing.

## Mandatory Safety References (read before acting)

- **`references/bridge-refresh-safety.md`** — Pre-Flight checks required before any `agentteams … --bridge-refresh` against an external project. `--bridge-refresh` is destructive at the target; default to `--bridge-merge` whenever target entry files exist without `AGENTTEAMS-BRIDGE` fences. Binds @orchestrator, @git-operations, @security, @cleanup.
