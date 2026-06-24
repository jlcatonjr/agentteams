# Bridge Entrypoint: copilot-vscode -> goose

This is a lightweight interface bridge.
Canonical agent definitions remain in source framework files.
Use orchestrator-first routing for team-based work.

## Retrieval Surface

Before falling back to grep / filesystem search for thematic or
cross-summary questions, query the agentteams memory-index:

```
agentteams --query-index "<the user's question>" --query-strategy vector --query-k 5
```

Some installations require `--description PATH` for read-only queries —
pass the project brief if so. The index covers durable prose (work
summaries, plans, CHANGELOG, references), NOT code. For code-symbol
lookups, grep remains primary.

See `domain-boundary.md` (this directory) for the boundary between the
memory-index vector mode and project-level retrieval-integrator
validation contracts — they address different questions and must not
be conflated.
