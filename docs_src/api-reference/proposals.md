# `proposals` — AgentTeamsModule

Orchestrator-only-writes pilot, phase P1. The orchestrator applies typed change and deletion proposals and
runs typed command requests for the agents it dispatched, under a policy registered in the brief.

> Source: `agentteams/proposals.py` · CLI: `agentteams/cli/proposal_commands.py` (both integrity-pinned)

---

Under the opt-in `write_policy: "orchestrator-only"` pilot, non-orchestrator agents don't edit files or run
commands. They return one of three artifacts:

| Artifact | Schema |
|---|---|
| Change proposal: `{kind, dispatch, path, base_sha256, content, rationale, gates?}` | `schemas/change-proposal.schema.json` |
| Deletion proposal: `{kind, dispatch, path, base_sha256, rationale}` | `schemas/delete-proposal.schema.json` |
| Command request: `{kind, dispatch, argv, purpose, cwd?, expected_writes?, stdin_from_content?}` | `schemas/command-request.schema.json` |

## Flow

1. **Dispatch.** `agentteams --issue-dispatch --agent SLUG --project ROOT` prints a nonce for the agent. The
   orchestrator includes it in the agent's task. Only `HMAC(key, nonce)` is stored, so reading
   `.agentteams/` reveals no usable nonce. A nonce allows 25 artifacts within 24 hours, and dry runs don't
   count against it. Expired dispatches are compacted away.
2. **The agent returns an artifact** carrying that nonce. If the artifact also has an `agent` field, it
   must match the dispatch record.
3. **The orchestrator runs** `agentteams --apply-proposal FILE` or `--run-request FILE`, with
   `--description BRIEF --project ROOT [--dry-run]`.
   - The agent is read from the signed dispatch record, so prose in a proposal cannot steer the
     orchestrator into acting as another agent.
   - `--dry-run` runs every check, *including the gates, which execute*, but changes nothing.
4. **Audit.** `agentteams --verify-proposal-ledger --project ROOT` checks the ledger.

Every operation needs an operator key: `AGENTTEAMS_PROPOSAL_LEDGER_KEY`, else
`AGENTTEAMS_DECISION_SIGNING_KEY`. Without one, everything is refused.

## Checks

**Proposals.**
- The path must not resolve outside the project.
- These paths are refused:
  - the control plane, case-folded: `.git`, `.claude`, `.goose`, `.codex`, `.github/agents`,
    `.github/hooks`, `sandbox`, `.agentteams`;
  - the brief itself;
  - anything matched by `protected_paths`. A pattern without wildcards also covers everything below it.
- The path must be inside the agent's `write_scopes`.
- Content is UTF-8 text within the size cap (256 KiB). A target that isn't UTF-8 is refused.
- The base must not be stale.
- Every requested gate, and every gate whose glob matches the path (case-insensitive), runs on a temporary
  copy of the proposed content.
- The destination is re-resolved and the base re-checked after the gates and immediately before an atomic
  write, which keeps the file's mode.

**Requests.**
- `argv` must start with one of the agent's prefixes, and each remaining argument must fullmatch its
  pattern. A `variadic` entry's last pattern covers one or more trailing arguments.
- An argument containing a `..` path segment is refused at run time, whatever the pattern admits.
- `argv[0]` is resolved on a sanitized PATH and must not live inside the project.
- `cwd` must be the entry's pinned `cwd`.
- `expected_writes` must lie in the entry's `writes` globs or in the agent's scopes, and never in the
  control plane.
- `stdin_from_content` is accepted only by entries that register `stdin_gates`, which run on it first.

**Environment.** Gates and commands get only locale and `HOME`-type variables plus a PATH of absolute
entries. Signing keys and tokens are never passed.

**Undeclared writes fail the run** (exit 3).
- **What is compared:** tracked and untracked files by content hash, and the watched control plane by
  size, mtime, ctime and inode, before and after the command.
  - The control plane covers `.agentteams`, `.claude`, `.goose`, `.codex`, `.github/agents`,
    `.github/hooks` and `sandbox`.
  - It also covers git's `hooks`, `config`, `config.worktree` and `info`, resolved with
    `git rev-parse --git-path`, so linked worktrees and `core.hooksPath` are watched too. If git can't resolve
    them (e.g. a git older than 2.31), the command is refused.
  - ctime cannot be set back by the user, so a same-size rewrite with its mtime restored still shows.
- **Timeouts:** a command runs in its own process group. On timeout the whole group is killed, then the
  command is still checked: its writes are compared and the ledger is tamper-checked before the failure is
  recorded.
- **No git worktree:** the command is refused (fail-closed).
- **Ledger tampering:** if the command changed the ledger, its head or the dispatch records, nothing more is
  recorded (`LedgerTamperedError`) and the operator must inspect. The write-ahead `run-request-start` row
  then has no matching completion row. That gap is the signal to look for.
- **Write-ahead:** a ledger row is written *before* every write, deletion and command run, so a crash leaves
  a recorded intent.
- **Blind spot:** files ignored by git (build directories) are invisible to this check.

**Policy lint** (`load_policy`) refuses:
- argument patterns that would admit an option (`-x`), a parent path (`../x`) or an absolute path;
- shells as commands or gates;
- `{file}`/`{path}` used inside a larger argument;
- write scopes that cover the brief.

## Residual risk (stated, not hidden)

- An allowed command that runs agent-written code is arbitrary execution, whatever the allowlist says.
  `lake build` compiles Lean that can do IO at compile time, and a test runner executes test files.
  Contain these with the OS sandbox profiles (P4).
- The undeclared-write check doesn't see paths ignored by git. Those belong to the sandbox's confined roots.
- **Until P4, the ledger signature is not a boundary against commands.** A command runs as the same user, so
  it can read its parent process's environment (on Linux `/proc/<pid>/environ`), and with it the key.
  A process that leaves its process group (`setsid`) can also outlive the after-snapshot.
- **Dry runs execute gates** and don't count against a nonce's use limit, so one nonce can trigger
  unlimited gate runs. Gates are registered by the operator, but they still run code on agent-supplied
  content.
- **Writes outside the project** (e.g. `~/.gitconfig`, shell rc files) are not detected by this check. Only
  the P4 sandbox stops them.
- The policy lint for argument patterns is a probe-based sample. It catches option-, parent-, absolute- and
  home-shaped arguments around common stems (`.*`, `[a-z].*`, `.+\\.lean`), but it is not a proof.

## Ledger

`.agentteams/proposal-ledger.jsonl` gets one row per dispatch, apply, deletion, run and refusal. Dispatch
issue, compaction and use counting serialize on `.agentteams/dispatches.lock`, a file that is never replaced,
so the use cap cannot be raced. Each row is
HMAC-signed and carries the hash of the previous row. Appends take a file lock. The head file
`.agentteams/proposal-ledger.head`, also signed, anchors the row count and last hash.
`--verify-proposal-ledger` reports an edited, removed, reordered or truncated ledger, and one rewritten
without the key.

## Public surface

- `issue_dispatch(root, agent)` → nonce; `agent_for(root, nonce)` → agent
- `load_policy(brief, *, brief_rel=None)` → `Policy`
- `apply_proposal(artifact, *, root, policy, dry_run=False)` → `{agent, path, base_sha256, new_sha256, gates, written}`
- `run_request(artifact, *, root, policy, dry_run=False)` → `{agent, argv, exit, stdout, stderr, undeclared_writes, ran}`
- `record(root, entry)`, `verify_ledger(root)`
- `ProposalError` (every refusal) and `UndeclaredWritesError` (the command ran; `.result` holds its output)

P1 does not change generation. The brief switch and the `AR_WRITE_POLICY` contract check (P2), template and
emitter changes (P3) and confined programs in the sandbox profiles (P4) follow.
