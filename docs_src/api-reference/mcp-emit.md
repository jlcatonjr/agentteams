# `mcp_emit`

Gated, **inert** MCP server-definition emitter. Writes
`.claude/mcp-servers.agentteams.json` — a list of server definitions (each
conforming to `schemas/mcp-server.schema.json`) plus a sibling `activation_status`
map. This file **provisions nothing**: it is documentation/configuration only,
contains no secrets, and is deliberately **not** named `.mcp.json` (the name
Claude Code auto-loads). Credentialed *activation* — writing a live `.mcp.json`
that wires real credentials to a network boundary — is a separate, operator-
authorized step that agentteams does not perform.

Opt-in via [`--target-host-features claude:mcp`](host-features.md) or
`bridge:copilot-vscode-to-claude:mcp`. Background:
`references/mcp-auto-detection-report.md` §5.4/§6.

## Specifying servers (the automation path)

An operator declares the servers they want under `mcp_servers[]` in the project
description (`schemas/project-description.schema.json`). The pipeline then:

1. [`analyze.build_manifest`](analyze.md) copies declared servers verbatim into
   the team-manifest's `mcp_servers[]` (inert; absent ⇒ manifest unchanged).
2. On the build/`--update` path, `cli.generate` calls `_write_mcp_servers` when an
   MCP host-feature token is enabled, which invokes `emit_mcp_artifact` and writes
   the inert artifact to the **project root** `.claude/` (a Claude-Code config
   location).

`mcp_servers` (emission input) is distinct from `mcp_hints` (detection input, see
[`mcp_detect`](mcp-detect.md)).

## Inertness is enforced, not assumed

Each server is checked before writing: non-dict shape, missing `server_id`, an
inline-secret-shaped `credential_ref` (a raw `scheme://user:pass@host` string is
rejected), and full `mcp-server.schema.json` validation when `jsonschema` is
available. Failures are routed to `result.errors` and skipped — never written.

`activation_status[server_id]` is computed **fail-closed**: a server is marked as
needing operator authorization unless it is unambiguously first-party with only
`read`/`write` tools and no `security_review.required`. `overwrite` defaults to
`False` so operator-authorization records are never clobbered on re-run.

## Public Surface

```python
@dataclass
class MCPEmissionResult:
    written: list[str]
    skipped: list[str]
    errors: list[str]
    gated_off: bool
    activation_blocked: list[str]
    success: bool  # property: len(errors) == 0
```

```python
mcp_enabled(features: list[str]) -> bool
```
True iff an MCP host-feature token is active (`claude:mcp` or
`bridge:copilot-vscode-to-claude:mcp`).

```python
emit_mcp_artifact(
    *,
    servers: list[dict],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
) -> MCPEmissionResult
```
Write `.claude/mcp-servers.agentteams.json` under `output_root` when MCP is
enabled. No-op (`gated_off=True`) when no token is active; no-op when `servers` is
empty; skips (does not clobber) an existing file unless `overwrite=True`.
Non-conforming entries are skipped into `result.errors`.

## Drift

The artifact is `.json` and is excluded from drift by construction — it is never
recorded in `output_files_map`/`template_hashes`/`file_hashes`, and the `.md`
snapshot suite is unaffected.
