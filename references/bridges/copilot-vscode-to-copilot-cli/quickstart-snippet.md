# Bridge Quickstart Snippet

Use this as your first prompt:

```text
Use the copilot-vscode agent infrastructure through this copilot-cli bridge.
Start with the source orchestrator and follow source governance rules.
Do not bypass orchestrator for multi-step, destructive, or cross-repo work.

Retrieval-first: for 'where is X' / 'have we seen Y before' / thematic
questions, run `agentteams --query-index "<question>" --query-strategy vector`
before grep. The memory-index covers durable prose (work summaries,
plans, CHANGELOG). See references/bridges/<src>-to-<target>/domain-boundary.md
for the boundary vs project-level retrieval contracts.
```
