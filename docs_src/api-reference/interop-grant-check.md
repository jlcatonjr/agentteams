# `interop_grant_check` — AgentTeamsModule

The per-agent checks a projection runs after rendering and before writing anything. A source agent's tool grant is a
binding capability declaration (C-3), so a projection must never widen it, must say so (and record the operator's
opt-in) when the target surface can't enforce it, and must not ship a Copilot body the platform could truncate.

> Source: `agentteams/interop_grant_check.py`. Callers: [`interop.import_from_cai`](interop.md) with a
> `ProjectionPolicy` (`run_interop`: strict; `multi_sync._project_and_rebaseline`: not strict), and
> `convert.convert_team` (`check_agent`, `canonical_tools_header`, `source_grant`). Direct `import_from_cai`
> callers that pass no policy are unchecked.

## What a projection checks

1. **No widening** (every path). `projected_grant` re-reads the rendered file the way its surface does, platform
   defaults included. Its canonical scopes must be a subset of the source's; on goose only, `read` and `search` count
   as one class (`agentteams_readfs` serves both). A projected tool name that maps to no canonical scope fails closed
   unless the source declares the same name (`source_names`); the target brief's own `agentteams_runner` grant is
   the one exception.
2. **Enforcement class** (strict). An agent narrower than `SURFACE_DEFAULT` (read, search, edit, execute) is refused
   on a `declared` or `none` surface unless the operator names it in `--interop-allow-cooperative`. The opt-in is
   recorded in the destination decision log (`record_opt_ins`).
3. **Copilot size** (strict). A `copilot-vscode`/`copilot-cli` body over `copilot_vscode._MAX_BODY_CHARS` (30,000)
   is refused. Native generation keeps its warning.
4. **goose readfs** (`prepare`). goose renders `goose_tool_scoping: "grant"`; when an agent reads, the server script
   at the target root must exist, be a regular file, and match `goose_tool_scoping.READFS_SHA256`.

Every refusal names the agent and the surface. All are collected and raised as one `ValueError`, and no agent file
is written.

## Constants

- `ENFORCED`, `DECLARED`, `NONE`: the enforcement classes.
- `SURFACE_DEFAULT`: `{"read", "edit", "search", "execute"}`, what a surface grants an agent that declares nothing.
- `DECISION_LOG_REL`: `references/security-decisions.log.csv`, relative to the destination agents directory.
- `OPT_IN_ACTION`: `interop-allow-cooperative`, the `action_reviewed` prefix of an opt-in row.

## Public types

### `ProjectionPolicy`

Frozen dataclass: `allow_cooperative: frozenset[str]` (the opted-in slugs) and `strict: bool = True` (also the
enforcement-class and Copilot-size refusals; `multi_sync` and `convert_team` use `False`).

### `ProjectionRun`

One projection's state, built by `prepare`: `annotate(body, slug, source_scopes)`, `check(slug, source_scopes,
rendered, raw_caps)` and `conclude(*, dry_run)`, which raises the collected refusals or records each applied opt-in
and returns the run's notices.

## Functions

### `parse_slug_list(value) -> frozenset[str]`

Parses `--interop-allow-cooperative` (comma-separated, repeatable). **Raises:** `ValueError` for an entry that is
not a plain slug. There is no wildcard.

### `enforcement_class(framework, manifest) -> str`

`none` for agents-md; for codex, `enforced` under its write-policy role gate (`codex_role_gate_enabled`), otherwise
`declared`; `enforced` for claude, copilot-vscode, copilot-cli and goose (whose grant binds on a direct
`goose run --recipe`).

### `narrower_than_default(scopes) -> bool`

True when the grant lacks any of `read`, `search`, `edit` or `execute`.

### `projected_grant(framework, rendered) -> tuple[frozenset[str], frozenset[str]] | None`

The canonical scopes a rendered agent grants on its surface, and its unmapped tool names (lower-cased):

| Surface | Read from | Default when undeclared |
|---|---|---|
| `claude` | the `tools:` scalar (`capability_map.claude_allowed_tools_to_canonical`, plus `*`, other `Bash(...)`, canonical tokens) | every tool (Claude inherits all) |
| `copilot-vscode`, `copilot-cli` | the `tools:` list (`capability_map.canonical_tools_for_copilot_vscode`) | the adapter's default `['read', 'edit', 'search']` |
| `goose` | `GooseAdapter.parse_agent_source` (`capability_map.goose_grants_to_canonical`); extension names | no `extensions` key: the whole `developer` |
| `codex` | `CodexAdapter.parse_agent_source` (the declared tools) | every tool (the agent inherits the session) |
| `agents-md` | none: returns `None` | n/a |

More than one `tools:` line reads as every tool. `projected_scopes` returns the scopes alone.

### `check_agent(slug, source_scopes, framework, rendered, manifest, policy, declared_names=()) -> list[str]`

The refusals for one agent; empty when it may be written. `source_scopes` is `None` when the source declared no grant
at all (no `tool_scopes` and no raw tools string); only the size check applies then.

### `annotate(body, slug, source_scopes, framework, manifest, policy) -> tuple[str, list[str]]`

Replaces any earlier projection banner in the body with the one this surface needs: the goose delegate caveat, or,
for an opted-in agent on a declared/none surface, the "grant is cooperative on this surface" banner plus a
`NOT ENFORCED` notice.

### `tools_line(framework, scopes, raw_caps, raw_scopes) -> str | None`

The `tools:` header line an imported agent gets (moved from `interop.import_from_cai`). A raw string is kept only
where it stays within the canonical scopes: verbatim on Copilot, and on Codex unless it names Claude tools, which
become canonical tokens so `sandbox_mode` and "Reading on Codex" render. Everything else gets the canonical bracket
list.

### `maps_to_canonical(raw) -> bool`, `canonical_tools_header(content) -> str`, `source_grant(content)`, `source_names(raw_caps)`

Helpers: whether a raw string is all known names with at least one Claude name; a source file with such a `tools:`
line rewritten as canonical tokens (`convert_team`); the scopes and names a Markdown source declares; the declared
names of `capabilities.raw`.

### `prepare(manifest, cai, framework, target_dir, policy) -> ProjectionRun`

Runs before any agent is rendered. For goose it sets `goose_tool_scoping: "grant"` and **raises** `ValueError`,
naming the agents, when one would read through `agentteams_readfs` and the server script
(`scripts/goose-readfs-mcp.py`, or `.agentteams/bin/goose-readfs-mcp.py` under the switch) is missing, linked, or
doesn't match `READFS_SHA256`. It also **raises** when an opt-in targets a declared/none surface whose destination
has no decision log.

### `record_opt_ins(team_dir, framework, entries, manifest) -> list[str]`

Appends one row per opted-in agent to `<team_dir>/references/security-decisions.log.csv` through
`governance_logs.append_row`, in the log's own header (legacy `timestamp,…,action_reviewed,verdict,…` or the
repository schema `date,…,decision,status,…`): action `interop-allow-cooperative:<framework>:<slug>`, verdict
`RECORDED` (not authorizing), the grant and enforcement class in `conditions`, and the `prev_digest` a hash-chained
log requires. **Raises:** `ValueError` for a missing, malformed or action-less log.

## See also

- [`interop`](interop.md), [`capability_map`](capability-map.md), [`interop_write_policy`](interop-write-policy.md)
- [Interoperability guide](../interoperability.md)
