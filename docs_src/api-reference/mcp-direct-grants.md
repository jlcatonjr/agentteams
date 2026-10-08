# `mcp_direct_grants` — AgentTeamsModule

Operator-signed direct-write grants (phase R5 of `references/plans/mcp-mediated-agent-writes.plan.md`). An agent
writing through the `agentteams_runner` MCP server is **staged** by default: the orchestrator approves each write. A
brief `mcp_grants` entry with `approval: "direct"` lets the agent's writes land at once. That removes the
orchestrator's look at each write, a constraint-relaxing change (Rule 15), so the runner honours it **only** with a
verified operator-signed grant.

> Source: `agentteams/mcp_direct_grants.py` (integrity-pinned). Operator commands are in
> `agentteams/cli/mcp_grant_commands.py`: `--sign-mcp-direct-grant`, `--list-mcp-direct-grants` and
> `--revoke-mcp-direct-grant`. Enforced by the runner (`proposal_runner`) and `proposal_staging.apply_direct`.

---

## Where grants live

`~/.config/agentteams/mcp-grants/<basename>-<hash>.json` sits outside the project. It is subject to the same custody
checks as the `confined_programs` file: a real file, owned by you, not group- or world-writable, outside the
project. The operator's public keys live in `~/.config/agentteams/verify-keys/<key_id>.pub.pem`. Under the switch,
Claude's built-in Edit and Write tools are denied both directories. A sandboxed shell (Claude's session sandbox, or
Goose's Seatbelt profile) can write only inside the project's write roots, so neither directory is writable from a
session, and a revoked grant can't be put back.

**Residual risk:** this depends on the session sandbox actually being merged. An orchestrator session running
unconfined could write `~/.config`. The runner's `direct-grants-loaded` row lists the trusted key fingerprints for
the operator to check.

## What a grant binds

Every field is Ed25519-signed, as one canonical JSON encoding, under its own purpose tag,
`agentteams-mcp-direct-grant-v1`. So distinct records never share a payload, and the signature can't be replayed as
any other artifact:
- grant id, agent, server and the exact tools;
- `approval: direct`;
- the agent's write scopes, and each team gate by a hash of its whole definition (a gate edited to always pass
  voids the grant);
- the installed server's sha256;
- issue and expiry times (at most `MAX_DAYS`, 30 days);
- a write cap, `max_writes` (at most 500).

Any drift between the grant and the live brief or installed server refuses it, and re-signing is the only way
back. A direct grant also needs a gate, or write scopes that name single files.

## Runner enforcement

- **At start:** it verifies every grant, enforces the aggregate cap (`MAX_ACTIVE` = 3; more fails closed), sets
  `policy.direct_agents`, ledgers `direct-grants-loaded`, and pins the file. A changed file needs a restart.
- **Counts from a verified ledger:** at start, if any grant is active, it verifies the whole ledger and counts each
  grant's `apply-direct` rows. A ledger that doesn't verify turns direct writes off. After that the count lives in
  memory, so deleting rows can't reset a grant's cap.
- **On every direct write:** it re-checks the expiry against `max_writes`, using the in-memory count. The ledger
  row records the grant id.
- **Visible keys:** `direct-grants-loaded` records the sha256 of each trusted operator verify key, so the operator
  can see which keys are trusted.
- **Narrower dispatches:** a direct agent's nonces live `DIRECT_TTL_HOURS` (4) with `DIRECT_MAX_USES` (10). A
  direct write whose nonce was issued with wider limits (before the grant loaded, say) is refused.
- **Deletions are never direct.** An agent without a verified grant is refused direct writes whatever the policy
  says.

## Public surface

| Name | Purpose |
|---|---|
| `PURPOSE_TAG`, `GRANTS_DIR`, `VERIFY_KEYS_DIR`, `MAX_ACTIVE`, `MAX_DAYS`, `MAX_WRITES`, `DIRECT_TTL_HOURS`, `DIRECT_MAX_USES` | The constants above. |
| `grants_file_for(root) -> Path` | The operator grants file for a project. |
| `signed_values(record) -> list[str]` | The exact signed payload. |
| `expected_binding(agent, policy, brief) -> dict` | What a grant for the agent must bind, from the live brief. |
| `read_grants(root) -> (records, sha)` | The custody-checked file. |
| `verify(record, policy, brief, *, now=None) -> dict` | Signature, binding, sunset and caps. |
| `active_grants(root, policy, brief) -> (agent -> grant, problems, sha)` | Every verified grant, under the aggregate cap. |
| `count_direct_writes(root) -> dict` | Each grant's direct writes, counted only from a verified ledger. |
| `check_write_allowed(grant, used, *, now=None)` | The per-write expiry and write-cap check. |
| `key_fingerprint(key_id) -> str` | The sha256 of a trusted verify key, for the start-up row. |
| `sign_record(record, private_pem, *, password=None) -> dict` | The operator signs; run outside every session. |
