# `mcp_need` — AgentTeamsModule

The agent MCP-need protocol for teams under `"write_policy": "orchestrator-only"`. It decides whether a
specific non-orchestrator agent needs an MCP server, and which one, from evidence in the runner's ledger
rather than from the agent's own account. It runs parallel to the skill capability-gap protocol.

> Source: `agentteams/mcp_need.py`. Shipped by `cli/render_pipeline.py` (the reference) and
> `liaison_logs.init_csv_stubs(..., mcp_needs=True)` (the register) only under the switch, so other teams
> are byte-identical. Context: [`write_policy`](write-policy.md), [`proposals`](proposals.md).

---

## Public surface

| Name | Purpose |
|---|---|
| `MCP_NEEDS_CSV` | `mcp-needs.csv`: the need register in the team's `references/`. Only the orchestrator writes it. |
| `MCP_NEEDS_HEADERS` | The register's columns. `verified` is `yes` only when `evidence` cites runner-ledger lines; an agent's gap note never counts as evidence by itself (C-4). |
| `REFERENCE_PATH` | `references/mcp-need.reference.md`. |
| `reference_doc() -> str` | The procedure: gap-note and ledger-evidence triggers; the ordered per-agent decision rule (security hard gate first, measured threshold, runner-path fixes, then a runner-hosted or passive-reader proposal with exact tool names); the gates; granting and retirement; the register. |

## What it changes in a generated team (under the switch)

- **Non-orchestrator agents:** the "Return Proposals" section tells them to attach a gap note to their handoff
  when they can't do what a task needs, instead of opening a plan or proposing a server.
- **The orchestrator:** the "Applying Proposals" section makes it the owner of every capability-gap plan, the
  skill protocol's included (other agents can't write files). It records gap notes in the register and
  decides per the reference.

Nothing here grants a capability. The per-agent MCP tool grant is added with the first approved server.

The evidence comes from [`mcp_need_report`](mcp-need-report.md) (`agentteams --mcp-need-report`).
