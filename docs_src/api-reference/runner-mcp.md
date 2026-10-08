# `runner_mcp` — AgentTeamsModule

The `agentteams_runner` MCP server: under `write_policy: "orchestrator-only"`, the **only** way a granted
non-orchestrator agent writes or executes (phase R4 of `references/plans/mcp-mediated-agent-writes.plan.md`). Its
tools queue requests for the out-of-session runner, which holds the ledger key, checks each request against that
agent's policy, and alone writes the project or runs commands.

> Source: `agentteams/runner_mcp.py` (install and launch facts) and `agentteams/data/agentteams-runner-mcp.py`
> (the server: stdlib only, no `agentteams` import). Both are integrity-pinned. Granted in the brief's `mcp_grants`.

---

## Tools

| Tool | Does | Runner kind |
|---|---|---|
| `read_file_hashed(path)` | Read a UTF-8 project file, up to 256 KiB, with its sha256: the `base_sha256` writes need. A missing file returns `"absent"`. Refuses VCS internals, the control plane, the queue and secrets. | none (local read) |
| `write_file(dispatch, path, content, base_sha256, rationale, gates?)` | Write a whole file. Staged, or applied at once for a `direct` instance whose grant the runner has verified. | `stage-proposal` / `apply-direct` |
| `delete_file(dispatch, path, base_sha256, rationale)` | Ask to delete. **Always staged**, even on a direct instance. | `stage-proposal` |
| `run_command(dispatch, argv, purpose, cwd?, expected_writes?, stdin_path?, stdin_content?)` | Run an allowlisted command in the agent's sandbox. Stdin goes only through `stdin_from_content` and its gates. | `run-request` |
| `request_status(request_id)` | A result this instance queued earlier, or `queued`. | none |

Each call waits up to `--wait` seconds (20 by default, 120 at most) for the runner's result. Otherwise it returns a
`request_id` to poll.

## Safety model

- **No key, no project writes.** The server's only writes are new files in `.agentteams-queue/mcp-requests/` and
  acknowledgements in `.agentteams-queue/acks/`.
- **Not a trust boundary.** The runner re-validates everything: identity from the nonce, the agent's scopes and
  allowlist, gates and base hashes.
- **Bound to one agent.** `--agent` is sent as `via_agent`. The runner refuses a nonce that belongs to another
  agent, and `request_status` answers only for ids this instance queued.
- **Only granted tools exist.** `--tools` lists them; the rest are neither advertised nor callable.
- **Launch integrity.** Generation installs the copy at `.agentteams/bin/agentteams-runner-mcp.py`, which is
  session-write-denied, and agents launch it with `python3 -I -S`. The runner checks it against `SHA256` on every
  poll (`proposal_runner.check_installed_servers`) and refuses to serve on a mismatch.

## Public surface (`runner_mcp`)

| Name | Purpose |
|---|---|
| `SERVER_NAME`, `TOOLS` | `agentteams_runner`, and the five tool names; they match `proposal_policy.MCP_RUNNER_TOOLS`. |
| `PROTECTED_PATH`, `PYTHON_FLAGS`, `SHA256` | Install path, launch flags and the pinned hash. |
| `server_content() -> str` | The shipped server's source. |
| `launch_args(agent, tools, approval="staged") -> list[str]` | The canonical arguments for one agent's instance. |
