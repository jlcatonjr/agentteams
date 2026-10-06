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

**Undeclared writes fail the run** (exit 3; a timeout exits 3 too).
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

## The switch and the `AR_WRITE_POLICY` check (P2)

`"write_policy": "orchestrator-only"` in the brief reaches the team manifest, and only that value does: a
brief without it, or with `"default"`, produces a byte-identical team. Under the switch, the post-generation
audit checks every agent file except the orchestrator.

This is a **static check of what agent files declare**, not runtime enforcement: it stops nothing at run
time. The runtime boundary is the proposal CLI plus, from P4, the OS sandbox profiles.

| Framework | Error | Warning |
|---|---|---|
| claude | Any tool that isn't known to be read-only (see below); a `tools:` key that is absent, null, empty (`[]`), duplicated, or of any shape other than one line or a clean `- item` block list, since the agent may then inherit every tool | `Bash` |
| copilot-vscode, copilot-cli | The same, plus `execute`, since Copilot has no per-agent sandbox | — |
| goose | A recipe whose real extensions or marker grant `edit`/`write`, `summon` or `sub_recipes`; any extension other than `developer` and `analyze` (judged by the tools they grant), `agentteams_readfs` and `agentteams_coordination`; missing `extensions` | A granted shell |
| codex | `sandbox_mode`, read as TOML, other than `"read-only"`, a missing key included; any `mcp_servers`; every `.toml` is checked, `references/` included | — |
| agents-md | Always: it declares no per-agent tools, so the policy can't be checked | — |
| any other | A shell too | — |

- **Read-only tokens:** `read`, `search`, `grep`, `glob`, `ls`, `todo`, `todowrite`, `web`, `fetch`, `webfetch`,
  `websearch`, `retrieval`, `codebase`, `usages` and `problems`. Everything else is refused, including
  dispatch (`Task`/`agent`, which can start a subagent that writes), `editFiles`, `runCommands`, MCP tools
  (`mcp__…`) and `*`.

- **Shell warnings can't be silenced yet.** Nothing confines a shell before the P4 sandbox profiles, so no
  brief field quiets them.
- **The orchestrator exemption** covers only the shallowest `orchestrator` file (on Goose, also
  `bridge-orchestrator`). A deeper copy, or two equally shallow copies, are checked like any agent. On
  Codex, the exempt file's `name` must also be `orchestrator`.
- **Coverage:** a disk audit (`--post-audit` on an existing team) sees every agent file in the team directory,
  adopted agents included, but skips symlinks and files that aren't UTF-8. The in-memory audit during
  generation sees only the rendered files.
- **Unreadable files are errors:** under the switch, a disk audit doesn't follow symlinks. It reports each
  one (file or directory), and each agent file it can't read or decode, since the host may still load it.
  Without the switch, the audit follows links as before.
- **Accepted residue:** under the switch, a Goose recipe can't carry any operator MCP server, even a
  read-only one, because the check can't tell what it does. The Goose extensions it does allow are trusted by
  name.
- **Not covered:** skills, and a project-level `.codex/config.toml` that overrides `sandbox_mode`. Codex's
  `sandbox_mode` is a default, not a ceiling.

## Generated teams under the switch (P3)

Under `"write_policy": "orchestrator-only"`, generation makes the team pass the check above.
`agentteams.write_policy.apply` runs before every framework adapter.

- **Non-orchestrator agents, the team builder included:**
  - Their canonical `tools:` line is narrowed to `read`, `search` and `todo`, and `read` is always kept.
    `edit`, `execute`, `agent` and `retrieval` are dropped.
  - Each framework derives its grants from that one line. Claude gets `Read, Grep, Glob`. Codex gets
    `sandbox_mode = "read-only"`. Goose, which is forced to grant mode (an explicit `"legacy"` is refused),
    gets `agentteams_readfs`. Copilot keeps the line as it is.
  - A "Write Policy: Return Proposals, Never Write" section is appended. It overrides any write wording
    in the body.
- **The orchestrator** (and Goose `bridge-orchestrator`) keeps its tools. It gains a "Write Policy:
  Applying Proposals" workflow:
  - issue a dispatch nonce, apply or run artifacts, never retry by hand, verify the ledger at closeout;
  - Workflows 0A and 0B members return proposals;
  - Workflow 13 (child orchestrators) is disabled.
- **The team ships** `references/write-policy.reference.md`, with the three artifact shapes and the exit
  codes.
- **Fences:** an unfenced body is wrapped whole, section included, so `--update --merge` refreshes both. A
  body that already has fences gets the section in its own `write_policy` fence.
- **Shell (`execute`)** is dropped everywhere until P4 can confine it (operator decision). Commands go
  through `command-request`.

- **Goose non-orchestrator recipes** carry no operator MCP server and no coordination server (it writes
  request and log files) under the switch.
- **A `tools:` key the generator can't narrow cleanly** is refused rather than shadowed by a second key. That
  means any shape other than a one-line flow list.

**Switching on an existing team.** `--update --merge` adds the sections, but front matter is never merged,
so the old tools stay. `--update` prints a notice saying so, and the audit flags them. Then run
`--reconcile-front-matter --reconcile-apply` to take the narrowed `tools:`.

**Not converted:**
- Adopted (bespoke) agents are never re-rendered. The audit flags them, and converting them is the
  consumer's job.
- Copilot handoff buttons stay. They're clicked by the user, not run by the agent.
- **Codex project-level MCP servers** (`codex:mcp`) are inherited by subagents, and `sandbox_mode` doesn't
  confine them. Keep them read-only, or off, for a team under the switch.

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
