# `proposal_staging` — AgentTeamsModule

Staged and direct writes for MCP-mediated agent writes (phase R3 of `references/plans/mcp-mediated-agent-writes.plan.md`).
Under `write_policy: "orchestrator-only"`, an agent granted the `agentteams_runner` MCP server sends its writes to the
runner:
- **Staged, the default:** the runner runs every check and gate, counts a nonce use, and stores the artifact
  (without its nonce) under `.agentteams/staged/`. The orchestrator approves or rejects it by id.
- **Direct:** only for an agent whose direct-write grant is verified (R5), applied at once.
- **Deletions are never direct** (C-5).

> Source: `agentteams/proposal_staging.py` (integrity-pinned). The validate-gate-act body is
> `proposals._apply`, shared by every mode, so the checks can't diverge. Served by the runner's `stage-proposal`
> and `apply-direct` kinds, which come only through the MCP channel, and by `apply-staged`, `reject-staged`,
> `list-staged` and `show-staged`, which only the orchestrator can queue. CLI: `--list-staged`, `--show-staged`,
> `--apply-staged`, `--reject-staged`.

---

## Public surface

| Name | Purpose |
|---|---|
| `STAGED_REL`, `STAGED_TTL_HOURS` | `.agentteams/staged`, and 24 h of approvability. |
| `stage_proposal(artifact, *, root, policy, confine=False, expect_agent=None) -> dict` | Validate and gate a change or deletion, count a use, store it, and return the receipt `{sid, agent, path, kind, base_sha256, new_sha256, bytes, gates, expires, staged: True}`. |
| `apply_direct(artifact, *, root, policy, confine=False, expect_agent=None) -> dict` | Apply a change at once. Refuses a deletion before identity is resolved, and refuses an agent not in `policy.direct_agents`. |
| `apply_staged(sid, *, root, policy, confine=False) -> dict` | The orchestrator's approval. Re-runs every check and gate against the file as it is now, with identity taken from the record. No use is counted, because staging counted it. The record is removed once applied. |
| `reject_staged(sid, *, root, reason="") -> dict` | Drop a record and ledger the rejection. |
| `list_staged(root) -> list[dict]` | Metadata only, oldest first. Malformed records are listed by id. |
| `show_staged(sid, *, root) -> dict` | One record in full, content included, for reading on cause. It never holds a nonce. |
| `stage(...)` | Internal: called by `proposals._apply` in stage mode. |

## Ledger rows

`stage-proposal`, then `apply-staged` or `reject-staged`, and `apply-direct`. Refusals are logged under the same
action names.
