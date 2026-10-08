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
> kind, which comes only through the MCP channel (the runner itself upgrades a staged change to a direct write when
> the agent's grant verified; `apply-direct` is not a request kind), and by `apply-staged`, `reject-staged`,
> `list-staged` and `show-staged`, which only the orchestrator can queue. CLI: `--list-staged`, `--show-staged`,
> `--apply-staged`, `--reject-staged`.

---

## Public surface

| Name | Purpose |
|---|---|
| `STAGED_REL`, `STAGED_TTL_HOURS` | `.agentteams/staged`, and 24 h of approvability. |
| `stage_proposal(artifact, *, root, policy, confine=False, expect_agent=None) -> dict` | Validate and gate a change or deletion, count a use, store it, and return the receipt `{sid, agent, path, kind, base_sha256, new_sha256, bytes, gates, expires, staged: True}`. |
| `apply_direct(artifact, *, root, policy, grant, used=0, confine=False, expect_agent=None) -> dict` | Apply a change at once, called by the runner only. Refuses a deletion before identity is resolved, and refuses without a verified grant record (`grant`). `used` is the runner's count of direct writes under it, from a verified ledger. |
| `apply_staged(sid, *, root, policy, confine=False) -> dict` | The orchestrator's approval. The record is signed with the runner's ledger key, so approval first verifies the signature, the record's own path and kind binding, the content against `content_sha256`, and the expiry. A same-user process could otherwise swap the content or the agent between staging and approval. It then re-runs every check and gate against the file as it is now, with identity taken from the record. No use is counted, because staging counted it. The record is removed once applied. |
| `reject_staged(sid, *, root, reason="") -> dict` | Drop a record and ledger the rejection. |
| `list_staged(root) -> list[dict]` | Metadata only, oldest first. Malformed records are listed by id; expired ones are purged, and the purge is ledgered as `expire-staged`. |
| `show_staged(sid, *, root) -> dict` | One record in full, content included, for reading on cause. It never holds a nonce. |
| `stage(...)` | Internal: called by `proposals._apply` in stage mode. |

## Ledger rows

`stage-proposal`, then `apply-staged` or `reject-staged`, and `apply-direct`. Refusals are logged under the same
action names.
