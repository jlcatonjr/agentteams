# `interop_grant_check` — AgentTeamsModule

The per-agent checks an `--interop-from` projection runs after rendering and before writing anything. A source
agent's tool grant is a binding capability declaration (C-3), so a projection must never widen it, must say so when
the target surface can't enforce it, and must not ship a Copilot body the platform could truncate.

> Source: `agentteams/interop_grant_check.py`. Called by [`interop.import_from_cai`](interop.md) when
> `run_interop` passes a `ProjectionPolicy` (every `--interop-from` run). `multi_sync` and direct
> `import_from_cai` callers pass none and are unchanged.

## What a projection checks

1. **No widening.** `projected_scopes` re-reads the rendered file the way its surface does, platform defaults
   included, and the result must be a subset of the source's canonical scopes. `read` and `search` count as one
   read-only class: every surface that grants one exposes the other's data (goose's `agentteams_readfs` serves
   both). Bespoke tool names (`runCommands`, MCP tools) have no canonical meaning and are not compared.
2. **Enforcement class.** An agent narrower than `SURFACE_DEFAULT` (read/search, edit, execute) is refused on a
   `declared` or `none` surface unless the operator names it in `--interop-allow-cooperative`.
3. **Copilot size.** A `copilot-vscode`/`copilot-cli` body over `copilot_vscode._MAX_BODY_CHARS` (30,000) is
   refused. Native generation keeps its warning.

Every refusal names the agent and the surface. `import_from_cai` collects them all, raises one `ValueError`, and
writes no agent file.

## Constants

- `ENFORCED`, `DECLARED`, `NONE`: the enforcement classes.
- `SURFACE_DEFAULT`: `{"read", "edit", "search", "execute"}`, what a surface grants an agent that declares nothing.

## Public types

### `ProjectionPolicy`

Frozen dataclass with one field, `allow_cooperative: frozenset[str]`: the slugs allowed onto a surface that can't
enforce their grant.

## Functions

### `parse_slug_list(value: str | Iterable[str] | None) -> frozenset[str]`

Parses `--interop-allow-cooperative` (comma-separated, repeatable). **Raises:** `ValueError` for an entry that is
not a plain slug. There is no wildcard.

### `enforcement_class(framework: str, manifest: dict) -> str`

`none` for agents-md; for codex, `enforced` under its write-policy role gate (`codex_role_gate_enabled`), otherwise
`declared`; `enforced` for claude, copilot-vscode, copilot-cli and goose (whose grant binds on a direct
`goose run --recipe`).

### `narrower_than_default(scopes: Iterable[str]) -> bool`

True when the grant lacks `edit`, `execute`, or read access.

### `projected_scopes(framework: str, rendered: str) -> frozenset[str] | None`

The canonical scopes a rendered agent grants on its surface:

| Surface | Read from | Default when undeclared |
|---|---|---|
| `claude` | the `tools:` scalar (Claude names) | every tool (Claude inherits all) |
| `copilot-vscode`, `copilot-cli` | the `tools:` list | the adapter's default `['read', 'edit', 'search']` |
| `goose` | `GooseAdapter.parse_agent_source` (`capability_map.goose_grants_to_canonical`) | no `extensions` key: the whole `developer` |
| `codex` | `CodexAdapter.parse_agent_source` (the declared tools) | every tool (the agent inherits the session) |
| `agents-md` | none: returns `None` | n/a |

More than one `tools:` line reads as every tool. **Raises:** `ValueError` from the goose/Codex reader on a file it
cannot parse.

### `check_agent(slug, source_scopes, framework, rendered, manifest, policy) -> list[str]`

The refusals for one agent; empty when it may be written. `source_scopes` is `None` when the source declared no grant
at all (no `tool_scopes` and no raw tools string); only the size check applies then.

### `annotate(body, slug, source_scopes, framework, manifest, policy) -> tuple[str, list[str]]`

Replaces any earlier projection banner in the body with the one this surface needs: the goose delegate caveat, or,
for an opted-in agent on a declared/none surface, the "grant is cooperative on this surface" banner plus a
`NOT ENFORCED` notice.

### `maps_to_canonical(raw: str) -> bool`

True when a raw `tools:` string names only known tools and at least one Claude name. Codex then gets canonical tokens,
so `sandbox_mode` and the "Reading on Codex" section render.

### `prepare(manifest, cai, framework, target_dir, policy) -> list[str]`

Runs before any agent is rendered. For goose it sets `goose_tool_scoping: "grant"` and **raises** `ValueError`,
naming the agents, when one would read through `agentteams_readfs` and the server script
(`scripts/goose-readfs-mcp.py`, or `.agentteams/bin/goose-readfs-mcp.py` under the switch) is missing at the target
root. Returns notices, such as an opt-in naming an agent the source team doesn't have.

## See also

- [`interop`](interop.md), [`capability_map`](capability-map.md), [`interop_write_policy`](interop-write-policy.md)
- [Interoperability guide](../interoperability.md)
