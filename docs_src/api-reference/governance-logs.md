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
| `LOG_NAMES`, `LOG_DIRS` | The logs (`security-decisions.log.csv`, `agentteams-remediation-log.csv`, `redteam-findings.log.csv`, `mcp-needs.csv`, `conflict-log.csv`) are looked for in `references/`, `.github/agents/references/` and `.claude/agents/references/`. So a project root and a team dir both work. |
| `log_paths(root) -> list[Path]` | Every log present under `root`, each resolved file once. |
| `check_log(path) -> list[str]` | Problems in one log. It checks three signs, because an unclosed quote in the last column swallows the rest of the file into that field without changing any field count: a record whose field count differs from the header's; a strict-parse error (a quote still open at end of file); and a field holding a line that starts a dated record. Empty when well-formed. |
| `check_logs(root) -> list[str]` | `check_log` over `log_paths(root)`. |
| `append_row(path, row)` | The validated way for code to add a row. It takes a list in header order or a dict of header names, and only `str` values. Under an exclusive `flock` (POSIX), it re-checks the log, renders the row in memory, refuses a value holding a dated-record line, and checks the row round-trips to one record of the header's width. Then it appends with one `os.write` on an `O_APPEND` descriptor, keeping line endings, so concurrent callers neither interleave nor tear. It never rewrites. Raises `ValueError` and leaves the file untouched otherwise. Rows written by hand bypass it; `check_logs` is what catches those. |
| `main(argv)` | CLI. `python -m agentteams.governance_logs check [ROOT]` reports malformed logs (exit 1 when any). `python -m agentteams.governance_logs append LOG --row-json JSON` appends one row through `append_row` (exit 1 when refused). The orchestrator uses `append` to record the rows that the read-only `@security` and `@conflict-auditor` return, since those agents cannot write. |
