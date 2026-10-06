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
| `.agentteams-queue/requests/<id>.json` | the orchestrator's CLI | `id` is 32 hex characters. Anything else, or a non-regular file, is ignored or dropped. |
| `.agentteams/queue-claimed/<id>.json` | the runner (atomic rename) | A claimed request can't be served twice. |
| `.agentteams/queue-results/<id>.json` | the runner | Sessions under the switch can't write `.agentteams/`, so a result can't be forged. |
| `.agentteams-queue/acks/<id>` | the CLI | Once acknowledged, the runner deletes the result, and an unacknowledged one after 300s. A raw dispatch nonce doesn't linger. |
| `.agentteams/runner.lock`, `.agentteams/runner.heartbeat` | the runner | One runner per project. The CLI refuses fast when no heartbeat is fresh. |

Requests carry only a kind and an artifact, never a root, brief or policy. The runner pins those at start,
and stops serving if the brief changes.

## Public surface

| Name | Purpose |
|---|---|
| `Runner(root, brief_path, policy, *, key_file=None)` | Takes key-file custody (`proposals.use_key_file`), the runner lock and the pinned brief hash. |
| `Runner.serve_once()` / `serve_forever()` / `close()` | Serve the queue. Kinds: `issue-dispatch`, `apply-proposal`, `verify-ledger`. Refused until P4b confines commands: `run-request`, and any proposal a gate would check. An unconfined gate is a child of the key holder and can load session-writable code. |
| `enqueue(root, request) -> str` | Queue a request (CLI side). Refuses when no runner is alive. |
| `wait_result(root, request_id, *, timeout=120.0) -> dict` | Wait for and acknowledge a result. |
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

- **The heartbeat.** After a crash it goes stale, and the CLI refuses with "no runner is serving this
  project". Restart the runner.
- **The lock.** `.agentteams/runner.lock` is never deleted, on purpose. Its presence keeps the CLI in queue
  mode, so a later session can't fall back to signing with an environment key.
- **Interrupted requests.** A request interrupted mid-claim stays in `.agentteams/queue-claimed/`, never
  served. Re-queue it.

## Residual risk

- **Anyone who can enqueue can mint identities.** Any process in the orchestrator's session that can
  write `.agentteams-queue/` can queue `issue-dispatch`. That is no worse than P1, where the orchestrator
  issued nonces directly.
- **Nonce window.** A dispatch nonce sits in a result file until it is acknowledged, at most 300s. Agents
  have read tools.
- **Queue contents are readable.** Requests in `.agentteams-queue/` can be read by every agent in the
  session, including a proposal's full content.
- **Acks aren't bound to their requester.** Another agent can acknowledge a result early, which deletes it.
  That is a denial of service, not a forgery.
- **POSIX only.** The switch is refused on frameworks whose session sandbox doesn't deny the key directory
  (copilot, codex, agents-md).
