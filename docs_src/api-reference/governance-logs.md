# `governance_logs` — AgentTeamsModule

Keeps the append-only governance CSV logs well-formed. These logs are local and often gitignored, and many
sessions append to them. One malformed row makes every later reader mis-parse the file, and a rewrite that
trusts the parse truncates it. On 2026-10-07, `references/security-decisions.log.csv` lost 13 records that way
and was rebuilt from session transcripts.

> Source: `agentteams/governance_logs.py`. Read by `--verify-integrity` (`cli/commands.py`). A full rewrite goes
> through `agentteams.atomicio.atomic_rewrite_csv_rows`.

---

## Public surface

| Name | Purpose |
|---|---|
| `LOG_PATHS` | The logs checked when present, relative to a project or team root: `references/security-decisions.log.csv`, `references/agentteams-remediation-log.csv`, `references/redteam-findings.log.csv`, `references/mcp-needs.csv`, `.github/agents/references/conflict-log.csv`. |
| `check_log(path) -> list[str]` | One problem per record whose field count differs from the header's (or for an unreadable or headerless file). Empty when well-formed. |
| `check_logs(root) -> list[str]` | `check_log` over every `LOG_PATHS` entry present under `root`. |
| `append_row(path, row)` | The safe way to add a row. It refuses a malformed log, takes a list in header order or a dict of header names, requires `str` values, renders the row in memory, and checks it round-trips to one record of the header's width. Only then does it append, keeping the file's line endings. It never opens the file for rewriting. Raises `ValueError` and leaves the file untouched otherwise. |
