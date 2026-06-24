# Bridge Quickstart Snippet

Use this as your first prompt:

```text
Use the copilot-vscode agent infrastructure through this goose bridge.
Start with the source orchestrator and follow source governance rules.
Do not bypass orchestrator for multi-step, destructive, or cross-repo work.

Retrieval-first: for 'where is X' / 'have we seen Y before' / thematic
questions, run `agentteams --query-index "<question>" --query-strategy vector`
before grep. The memory-index covers durable prose (work summaries,
plans, CHANGELOG). See references/bridges/<src>-to-<target>/domain-boundary.md
for the boundary vs project-level retrieval contracts.
```

## Bridge check scope

`--bridge-check` verifies that source `.agent.md` files match their
SHA-256 hashes recorded at bridge-generation time. It does NOT validate
generated recipe YAML files, `.goosehints` enrichment, or AGENTS.md content.
To validate recipe structure: `agentteams --framework goose --recipe-check --output <recipes-dir>`
checks version string, no model: key, sub_recipe path resolution, and non-empty instructions.
For full recipe generation (alternative to bridge): `agentteams --convert-from .github/agents --framework goose --output .goose/recipes`

## CLI + MCP entry recipe

The bridge emits `.goose/recipes/bridge-orchestrator.yaml` — run it with
`goose run --recipe .goose/recipes/bridge-orchestrator.yaml` to start the
bridged team WITH the `developer` (CLI) extension by default. Pass
`--target-host-features bridge:<source>-to-goose:mcp` and build the source
with an MCP token first to also wire the selected (first-party, read-only,
orchestrator-scoped) MCP servers into that recipe.
