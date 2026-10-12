# `proposal_runner` — AgentTeamsModule

Orchestrator-only-writes pilot, phase P4a. This is the out-of-session runner: it alone holds the ledger key
and serves the orchestrator's queued requests.

> Source: `agentteams/proposal_runner.py` (integrity-pinned). CLI: `agentteams --serve-requests`,
> `--wait-result`. Background: [`proposals`](proposals.md#out-of-session-runner-p4a).

---

## Why it exists

Live probes on macOS (2026-10-06) found two things:

- **Environment keys leak.** A sandboxed child reads a same-user parent's exec-time environment through
  `KERN_PROCARGS2`, and no Seatbelt rule stops it. So a key in an environment variable can't be protected,
  while a key in a file whose directory the sandbox denies can.
- **Sandboxes don't nest.** `sandbox-exec` can't apply a profile from inside an already-sandboxed process,
  so an orchestrator inside its session sandbox can't confine the commands it runs.

So the key holder and the confinement point run outside every agent session.

## Queue

| Path | Written by | Notes |
|---|---|---|
| `.agentteams-queue/requests/<id>.json`, then `<id>.ready` | the orchestrator's CLI | `id` is 32 hex characters. Anything else, or a non-regular file, is ignored or dropped. The **orchestrator channel**. Each file is written with one exclusive create: inside a Claude session the queue is read-denied, which still allows creating a file there but not renaming one in (measured 2026-10-11). The runner serves a request once its `.ready` marker exists (or, with no marker, after `STALE_REQUEST_SECONDS`, when an old client is refused). Each request carries a fresh `reply_key`, which the runner removes before handling it. |
| `.agentteams-queue/mcp-requests/<id>.json` | the `agentteams_runner` MCP server | The **MCP channel** (R2). The runner reads a request's channel from the directory it arrived in, never from a field. This channel may queue only `stage-proposal` and `run-request`. Each request names its server instance's agent (`via_agent`), which must equal the agent its nonce was issued to. Its commands are capped at `MCP_COMMAND_TIMEOUT`. An agent listed in the policy's `mcp_agents` is refused on the orchestrator channel. |
| `.agentteams/queue-claimed/<id>.json` | the runner (atomic rename) | A claimed request can't be served twice. |
| `.agentteams-status/heartbeat`, `.agentteams-status/results/<id>.sealed` | the runner | The **status tree**: the session may read it but never write it (`Edit(/.agentteams-status/**)` and sandbox `denyWrite`). Claude Code applies the session's `Read(/.agentteams/**)` deny to sandboxed commands too, so the orchestrator's CLI can't read `.agentteams/`: it reads liveness and results here instead (biographies pilot, 2026-10-11). Each orchestrator result is **sealed** with its request's `reply_key` ([`sealed_result`](#sealed-results)), so other agents reading the tree learn nothing. At most `SEALED_RESULT_CAP` (500) are kept; each expires after 300 s. `.agentteams-status/.gitignore` is generated with the team, so the tree exists before any sandbox names it and its contents stay untracked. |
| `.agentteams/queue-results/<id>.json` | the runner | The MCP channel's results (its server runs unsandboxed), and the refusal for an orchestrator request with no `reply_key`. Sessions under the switch can't write `.agentteams/`, so a result can't be forged. |
| `.agentteams-queue/acks/<id>` | the CLI | Once acknowledged, the runner deletes the result, and an unacknowledged one after 300s. A raw dispatch nonce doesn't linger. |
| `.agentteams/runner.lock`, `.agentteams/runner.heartbeat` | the runner | One runner per project. The legacy heartbeat is still written for the pinned MCP server; the CLI reads `.agentteams-status/heartbeat` and refuses fast when it isn't fresh. |

Requests carry only a kind and an artifact, never a root, brief or policy. The runner pins those at start,
and stops serving if the brief changes.

## Public surface

| Name | Purpose |
|---|---|
| `Runner(root, brief_path, policy, *, key_file=None)` | Takes key-file custody (`proposals.use_key_file`), the runner lock and the pinned brief hash. |
| `Runner.serve_once()` / `serve_forever()` / `close()` | Serve the queue. Kinds: `issue-dispatch`, `apply-proposal`, `run-request`, `verify-ledger`. Every command and gate runs confined (P4b, [`confinement`](confinement.md)). An unconfined child of the key holder could load session-writable code and read the key. |
| `enqueue(root, request, *, channel="orchestrator") -> str` | Queue a request on a channel's directory (`"orchestrator"` or `"mcp"`). Refuses when no runner is alive. On the orchestrator channel it adds a fresh `reply_key`, held in this process's memory only, never on disk or stdout. |
| `wait_result(root, request_id, *, timeout=120.0) -> dict` | Wait for, open and acknowledge a result. From another process (`--wait-result`), which never had the reply key, it reports only that the runner finished (`{"sealed": True, "ok": None}`); re-query to see the outcome. |
| `poll_result(root, request_id) -> dict \| None` | The result if ready (opened and acknowledged), else `None`. Never blocks. Raises when the runner stopped answering, or when a sealed result fails verification. |
| `MCP_COMMAND_TIMEOUT` | 120 s: the command cap for requests on the MCP channel (`mcp-requests/`). The queue is served one request at a time, because each command's write check diffs the worktree before and after it runs, so this bounds how long one agent's command holds the others. |
| `runner_alive(root) -> bool` | Whether a heartbeat is fresh. |
| `RunnerError` | A `ProposalError`: no runner, a second runner, a changed brief, a bad request. |

## Start-up checks

The runner refuses to start when:
- the brief doesn't set `write_policy: "orchestrator-only"`;
- a queue directory is a symlink;
- another runner holds the lock;
- the key file is unusable. It must sit in `~/.config/agentteams/keys`, be owned by you, have mode 0600, and
  not be a symlink.

## Crash recovery

- **The heartbeat.** Since R1 a background thread refreshes it every `HEARTBEAT_INTERVAL_SECONDS` (3 s),
  independent of serving, so a long command no longer makes the runner look dead to agents queued behind it.
  After a crash it goes stale, and the CLI refuses with "no runner is serving this project". Restart the
  runner.
- **The lock.** `.agentteams/runner.lock` is never deleted, on purpose. Its presence keeps the CLI in queue
  mode, so a later session can't fall back to signing with an environment key.
- **Interrupted requests.** A request interrupted mid-claim stays in `.agentteams/queue-claimed/`, never
  served. Re-queue it.

## Residual risk

- **Anyone who can enqueue can mint identities.** Any process in the orchestrator's session that can
  write `.agentteams-queue/` can queue `issue-dispatch`. That is no worse than P1, where the orchestrator
  issued nonces directly.
- **Nonce window.** A dispatch nonce sits, sealed, in a result file until it is acknowledged, at most 300s.
  Only the process that queued the request can open it.
- **The sandbox can't tell agents apart.** Any process in the session with a shell can create a request and read
  its own sealed result, which is the orchestrator's whole channel. The audit therefore makes a shell on any
  non-orchestrator agent an error under the switch (AR_WRITE_POLICY).
- **Built-in subagents (residual).** Claude Code's built-in general-purpose subagent holds Bash, and agentteams
  doesn't render it, so the audit can't narrow it, and no verified permission rule denies it. If the orchestrator
  dispatches it, it has the orchestrator's channel, as the orchestrator itself does. Under the switch the
  orchestrator dispatches only the team's own agents; treat a general-purpose dispatch as acting as the
  orchestrator.
- **Linux.** The status channel is measured on macOS (Claude Code 2.1.246). Under Linux bwrap a read-denied
  queue might be masked rather than refused, so `--check-wiring` refuses Linux until it is measured.
- **Queue contents.** Under the emitted Claude settings, no agent in the session can read `.agentteams-queue/`
  (including the orchestrator). Without those settings, requests (and their reply keys) are readable.
- **Acks aren't bound to their requester.** Another agent can acknowledge a result early, which deletes it.
  That is a denial of service, not a forgery.
- **POSIX only.** The runner needs `fcntl` and `O_NOFOLLOW`.
- **Claude and goose only.** The switch is refused on frameworks whose session sandbox doesn't deny the key
  directory (copilot, codex, agents-md).

## Sealed results

`agentteams/sealed_result.py` (integrity-pinned, stdlib only) seals an orchestrator result for the process that
queued it:
- **Keys.** The `reply_key` is 32 bytes from `secrets.token_bytes`. Separate encryption and MAC keys are derived
  from it with labelled HMAC.
- **Encryption.** HMAC-SHA256 in counter mode over a random 16-byte IV.
- **Integrity.** A full 32-byte tag over `magic ‖ request id ‖ IV ‖ ciphertext`, checked with
  `hmac.compare_digest` before decryption, so a forged blob, or one for another request, is refused.
- **Padding.** The plaintext is padded to fixed buckets (4 KiB, 64 KiB, about 2 MiB), so a blob's size reveals
  only its bucket.
