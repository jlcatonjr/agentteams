# `mcp_need_report` — AgentTeamsModule

`agentteams --mcp-need-report`: the evidence side of the agent MCP-need protocol (phase N3). Under
`write_policy: "orchestrator-only"`, whether a non-orchestrator agent needs an MCP server is decided from the
runner's ledger, never from the agent's own account. This module summarizes that ledger and the need register
per agent. It is read-only and decides nothing.

> Source: `agentteams/mcp_need_report.py`. Dispatched by `cli/app.py`. Procedure: the generated
> `references/mcp-need.reference.md` ([`mcp_need`](mcp-need.md)). CLI: [`--mcp-need-report`](../cli-reference.md).

---

## Public surface

| Name | Purpose |
|---|---|
| `LEDGER_REL`, `HEAD_REL`, `DISPATCH_REL` | The runner ledger and its signed head anchor, relative to the project root. They are restated rather than imported, because `proposals` needs POSIX-only `fcntl`; a test keeps them equal. |
| `MAX_FIELD`, `MAX_ROWS` | Text-output caps: 200 characters per field, 20 rows per agent and section. |
| `ReportPathError` | Raised for a symlinked path (dangling included), or one that resolves outside the project. |
| `check_chain(root) -> list[str]` | The keyless integrity checks: the `prev` hash chain, the head anchor's `count`/`last`, rows without a `mac` (blank lines count as rows), and a ledger removed while `dispatches.jsonl` remains. Returns the problems found (none when consistent). |
| `REGISTER_CANDIDATES` | Where the register is looked for, in order: `references/`, `.claude/agents/references/`, `.github/agents/references/`, `.goose/references/`. |
| `DEFAULT_PREFIX_WORDS`, `DEFAULT_MIN_REPEATS` | 3 leading argv words identify "the same command"; 2 runs make it "repeated". |
| `OPEN_STATUSES` | Register statuses reported as open: `open`, `waiting`, `proposed`, `approved`. |
| `build_report(root, *, register=None, prefix_words=3, min_repeats=2) -> dict` | Per agent: repeated `commands` (count, total and median ms, and failures: non-zero exit, timeout, undeclared writes, killed survivors); `unfinished` runs; `proposals` (count, total and median bytes); `refused` count; `register` rows split by the register's own `verified` label. Also returns `chain_problems`, `unreadable_rows` and `signatures_verified: False`. Raises `ValueError` (a bad count) or `ReportPathError`. |
| `format_report(report) -> str` | Plain text. The first line is the chain/head result. Each repeated command is followed by the runner-path fix to try first. Untrusted text is escaped and capped. |
| `run(root, *, register=None, as_json=False) -> int` | The CLI entry. Prints text or JSON. Returns 1 when the chain or head is inconsistent, or a path is refused, else 0. |

## Limits

- **Signatures unchecked.** The rows are HMAC-signed with the runner's key, which no session holds;
  `--verify-proposal-ledger` checks them. The keyless chain/head check catches careless or partial edits
  (deletions, reordering, insertions, truncation). It is not proof against deliberate forgery: the chain is
  unkeyed, so anyone who can write the files can recompute it.
- **Grouping** is by leading argv words, which is a proxy. `python3 -m pytest` runs merge into one group, and
  per-file arguments can split one. Grouping by the matched allowlist entry would need a new ledger field
  (a follow-up).
- **Refused requests** can't be grouped by command, since the ledger keeps only a reason.
- **In-session MCP calls** (a passive reader) never reach the ledger. They appear only if that server keeps its
  own call log, which the protocol requires before such a grant.
