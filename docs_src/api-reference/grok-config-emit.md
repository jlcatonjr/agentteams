# `grok_config_emit`

The agentteams-managed block in a project's `.grok/config.toml`, for xAI Grok Build (`--framework grok`,
Phase 2). A project `.grok/config.toml` may set only `[mcp_servers]`, `[plugins]` and `[permission]`. This module
owns one block holding up to two of them.

The block sits between these two marker lines, and every key outside it is left as it was:

```
# --- BEGIN agentteams-managed grok config (…) ---
# --- END agentteams-managed grok config ---
```

Writes go through `codex_mcp_emit.splice_managed_toml`. The Codex emitter uses the same function, so the
content-preservation check is shared: if any top-level key the block does not own would change, nothing is
written. A symlinked `.grok` dir or config file is refused.

## `[mcp_servers.<id>]` (host feature `grok:mcp`)

Operator-declared `mcp_servers[]` are wired only when they meet the Codex/Goose auto-wire bar
(`codex_mcp_emit.mcp_wirable`): first-party, every tool read-only, and no `security_review.required`.
`.grok/config.toml` is a live config that Grok loads after folder trust.

| Transport | Fields |
|---|---|
| stdio | `command`, `args`, and `env = {REF = "${REF}"}` when the server declares an `env` credential |
| http | `url`, plus `bearer_token_env_var = "REF"` when it declares one |

Grok expands `${VAR}` in `url`, `command`, `args`, `env` and `headers` at load time (Grok user guide,
07-mcp-servers). Therefore:
- `${…}` appears **only** in `env` values, built from a credential reference that must match
  `^[A-Z_][A-Z0-9_]*$` (no `:-default`);
- a `command`, `args` entry or `url` containing `${` is refused;
- credentials are never written inline;
- `secret-store` and `oauth` mechanisms are not wired.

Default catalogue servers are withheld unless opted in, as for Codex: Grok applies project MCP servers to the
session, and generated subagents carry `mcpInheritance: none`.

## `[permission]` (host feature `grok:sandbox`)

`grok_permission_table(wired_ids)` builds the table:

- **`deny`:** the canonical control-plane set from `_sandbox_emit.permission_deny_rules("claude")`, plus
  `.grok/hooks/**` and `.github/hooks/**`.
  - That set covers every team's switch, rosters, verify-key store and team marker; `.grok/config.toml`;
    `.grok/sandbox.toml`; Claude's settings and hooks; and the operator key directories.
  - Each rule is rewritten as an unrooted `**/` glob. Grok joins a relative pattern with the session's working
    directory and treats a leading `~/` as literal text, so `Read(**/.config/agentteams/keys/**)` is how the
    home key directory is matched.
- **`ask`:** `MCPTool(<id>__*)` for every wired server. This is the counterpart of Codex's per-server
  `default_tools_approval_mode = "prompt"`.

If the file already has an operator `[permission]` table, the block omits its own and returns a notice: TOML
allows one table. That covers a `[permission]` header, dotted `permission.*` keys and `[permission.*]` subtables,
all detected with `tomllib`.

**Enforcement class: DECLARED.** The rules govern Grok's Read/Edit/Write tools, not shell writes. Grok also loads
project rules only after folder trust (spike R4). The OS boundary for shell writes is the agentteams launcher
(`.grok/confined-run.example.sh` → `sandbox/confine-run.sh`), not Grok's sandbox profile: spike R3 showed `read_only`
does not write-protect inside the workspace.

## API

| Name | Purpose |
|---|---|
| `emit_grok_config(*, servers, features, output_root, dry_run=False) -> GrokConfigResult` | Write the block. Result fields: `written`, `errors` (non-empty means refused), `notices`, `wired`, `not_wired`, `dropped_unmanaged`, `permission_written` |
| `grok_mcp_entry(server)` | One server → `(fields, None)`, or `(None, reason)` |
| `grok_permission_table(wired_ids)` | The `[permission]` table |
| `grok_mcp_enabled(features)`, `grok_sandbox_enabled(features)` | Feature-token gates |

Called from `cli/artifacts._emit_grok_config_if_enabled`, which is host-scoped like the Codex splice. The module
is integrity-pinned.
