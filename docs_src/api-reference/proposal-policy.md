# `proposal_policy` — AgentTeamsModule

Orchestrator-only-writes pilot: the policy half of [`proposals`](proposals.md). It was carved out under
CH-07 when P4b pushed `proposals.py` to its ceiling.

> Source: `agentteams/proposal_policy.py` (integrity-pinned). `agentteams.proposals` re-exports every name
> below, so callers import from there.

It reads and validates the brief, and nothing else: it writes nothing and runs nothing.

| Name | Purpose |
|---|---|
| `load_policy(brief, *, brief_rel=None, confined_file=None) -> Policy` | Validate `agent_policies`, `proposal_gates`, `protected_paths`, `confined_programs` and `allow_unconfined_runs`. Lint argument patterns (probe-based), `writes` globs, pattern `write_scopes` (P5b), gate argv, and confined exec/write paths ([`confinement.load_confined`](confinement.md)). `confined_file` is `(confined_programs, sha256)` from the operator-owned file. It replaces the brief's block, and both at once are refused. |
| `Policy` | The validated policy: `agent_policies`, `gates`, `protected_paths`, `size_cap`, `brief_rel`, `confined`, `allow_unconfined`, `confined_file_sha` (P5b: the operator file's hash the runner pins, or `None` when the brief's block applies). |
| `ProposalError` | A proposal, request or policy is refused; the message says why. |
| `DEFAULT_SIZE_CAP` | 256 KiB proposal size cap. |
