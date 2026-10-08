# `proposal_run` — AgentTeamsModule

The command-request half of the orchestrator-only-writes engine, carved out of `proposals.py` at R2 to stay under the
1,000-line module ceiling (CH-07). Integrity-pinned.

> Source: `agentteams/proposal_run.py`. The public entry point is unchanged:
> [`proposals.run_request`](proposals.md), which delegates here. Every shared helper and constant is reached through
> `proposals` at call time, so patching `proposals.COMMAND_TIMEOUT` and the like still takes effect.

---

## Public surface

| Name | Purpose |
|---|---|
| `run_request(artifact, *, root, policy, dry_run=False, confine=False, timeout_cap=None, expect_agent=None, refuse_agents=frozenset())` | The implementation behind `proposals.run_request`. It validates a `command-request` against the agent's allowlist and declared writes, runs it in the agent's `confined_programs` sandbox (the runner passes `confine=True`), diffs the worktree before and after to find undeclared writes, and ledgers the run. A nonce use is counted only once every check passes. Output is capped per stream at `proposals.MAX_OUTPUT_BYTES`. `timeout_cap` bounds the entry's timeout; the runner sets it for MCP-channel requests. The identity parameters carry the runner's channel rules (R2). |
