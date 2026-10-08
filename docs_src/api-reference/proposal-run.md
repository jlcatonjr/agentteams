# `proposal_run` — AgentTeamsModule

The command-request half of [`proposals`](proposals.md): it validates, runs and checks one allowlisted command an
agent asked for. Carved out of `proposals` at phase R2 (CH-07).

> Source: `agentteams/proposal_run.py`. The public entry point is `proposals.run_request`, which delegates here.
> Every shared helper and constant is reached through `proposals` at call time, so patching
> `proposals.COMMAND_TIMEOUT` and the like still takes effect. Served by the runner's `run-request` kind
> ([`proposal_runner`](proposal-runner.md)).

---

## Public surface

| Name | Purpose |
|---|---|
| `run_request(artifact, *, root, policy, dry_run=False, confine=False, timeout_cap=None, expect_agent=None, refuse_agents=frozenset()) -> dict` | Run one `command-request` artifact. Returns `{agent, argv, exit, duration_ms, stdout, stderr, truncated, undeclared_writes, ran}`. With `dry_run`, every check runs but the command does not (`ran: False`). |

## What a request must pass, in order

1. **Shape and identity.** The artifact is a `command-request`, and its agent identity checks out (`expect_agent`,
   `refuse_agents`).
2. **Arguments.** `argv` is a non-empty list of strings, never a shell string, and `purpose` is non-empty.
3. **Allowlist.** `argv` matches an entry on the agent's command allowlist. `cwd` must equal the entry's pinned cwd.
4. **Declared writes.** Each `expected_writes` path is inside the project. It matches the entry's `writes` globs or
   passes the ordinary scoped-write check, and it is never in the control plane or the brief that defines the policy.
5. **Confinement** (`confine=True`). The agent needs a `confined_programs` entry, the program must sit inside its exec
   paths, and its write roots may not cover a protected path or the brief. The runner runs nothing unconfined.
6. **Stdin content.** `stdin_from_content` must be exactly `{path, content}`, and the entry must register
   `stdin_gates`. The content goes through those gates before it reaches the command.

## Running and checking

- The project must be a git worktree; otherwise the run refuses (fail-closed). A write-ahead `run-request-start` row
  is recorded, then the worktree is snapshotted, so only the command's own changes count.
- The command runs in its own process group with a scrubbed environment and `shell=False`. The timeout is the
  entry's `timeout` (default `COMMAND_TIMEOUT`), capped at `MAX_COMMAND_TIMEOUT` and at `timeout_cap` when given.
  On a timeout the whole group is killed and the writes are still checked.
- Under confinement, anything the command left running is killed and the run fails. Changes inside the declared
  write roots count as declared only when a sandbox actually ran.
- **Undeclared writes** raise `UndeclaredWritesError` with the full result attached. A command that changed the
  ledger, head or dispatch files raises `LedgerTamperedError`, and nothing more is recorded.
- Other refusals raise `ProposalError` and are recorded as a refusal row (not under `dry_run`). Output is capped,
  with `truncated` set when it was cut.
