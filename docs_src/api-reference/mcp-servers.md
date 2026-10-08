# `mcp_servers` — AgentTeamsModule

agentteams' first-party MCP servers, run by `agentteams --serve-mcp NAME`. Each is stdlib-only and read-only.
None opens a network connection or holds a key. The catalogue entries that bind them to agents are in
[`mcp_catalog`](mcp-catalog.md).

> Source: `agentteams/mcp_servers/` (`__init__`, `_stdio`, `recall`, `gitread`). Dispatched first by
> `cli/app.py`, so nothing else writes to the protocol's stdout. CLI: [`--serve-mcp`](../cli-reference.md).

---

## Public surface

| Name | Purpose |
|---|---|
| `SERVERS` | `recall`, `gitread` → their modules. |
| `run_server(name, root) -> int` | Serves one server over stdio until stdin closes. Returns 2 for an unknown name. |
| `_stdio.serve(name, tools, call, root, *, stdin=None, stdout=None) -> int` | The newline-delimited JSON-RPC loop: `initialize`, `tools/list`, `tools/call`, `ping`. An unknown method gets -32601; a malformed or over-1-MiB line gets -32700; a `ToolError` becomes an error result. Results are capped at 64 KiB. |
| `_stdio.ToolError` | A tool refused or failed; its message goes back to the client. |
| `recall.call(name, arguments, root)` | `query_index(query, k ≤ 20)` and `query_code(query, k ≤ 20, kind)`. Reads the existing indexes from the first of `TEAM_DIRS` that has them and **never refreshes** a stale one. It has its own loaders: schema validation is uncached (the CLI's loaders write a `.vcache` sidecar), and every path, partition files included, must resolve inside the project. Returns `built_at`, `may_be_stale: true` and hits limited to an allowlist of fields, with snippets capped at 400 characters. |
| `gitread.call(name, arguments, root)` | `git_log(rev, n ≤ 200, path)`, `git_show(rev, path)`, `git_diff(base, head, path)`, `git_blame(rev, path)`, `git_status()`. Returns `{"output": text}`, capped at 60 KiB. |

## gitread isolation (@security C2, C6)

Repository-local git config can make even read commands run programs or reach the network. gitread therefore:
- runs `git` from argv lists only, with an allowlisted environment (`PATH`, a C locale,
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CEILING_DIRECTORIES`, `GIT_NO_LAZY_FETCH=1`,
  `GIT_TERMINAL_PROMPT=0`, `GIT_LITERAL_PATHSPECS=1`);
- always passes `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c core.pager=cat -c diff.external=
  -c core.untrackedCache=false -c log.showSignature=false -c protocol.allow=never
  -c status.submoduleSummary=false -c log.mailmap=false -c mailmap.file= -c mailmap.blob=`, and
  `-c protocol.<name>.allow=never` for file, git, ssh, http, https and ext (a repository's per-protocol setting
  outranks `protocol.allow`; git older than 2.44 ignores `GIT_NO_LAZY_FETCH`), plus `--no-pager --no-replace-objects --no-optional-locks` and an
  explicit `--work-tree`;
- passes `--no-ext-diff --no-textconv --no-color --ignore-submodules=all` where they apply, and uses no `%G`
  format;
- refuses a launch directory that is not a work-tree root; a `.git` file or symlink, a `commondir`, or object
  alternates that reach a repository outside the project (a registered linked worktree, whose admin dir sits
  under its common dir with a matching back-link, is allowed); and any repository whose config defines
  `filter.*` (includes followed);
- requires revisions for blame and diff (no worktree reads);
- validates refs (`[A-Za-z0-9._/@{}~^-]`, never a leading `-`) and keeps paths relative, inside the project
  and out of `.git`;
- bounds every call with a 15-second timeout.
