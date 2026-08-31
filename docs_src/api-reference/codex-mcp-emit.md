# `codex_mcp_emit`

MCP server emission into Codex's `.codex/config.toml` (open-items remediation OPEN-6).
Verified against OpenAI's Codex MCP documentation (2026-08-10): servers live under
`[mcp_servers.<id>]` tables; stdio servers use `command`/`args`/`env_vars`; HTTP
servers use `url`/`bearer_token_env_var`.

Unlike [`mcp_emit`](mcp-emit.md)'s inert JSON sidecar (never auto-loaded by Claude
Code), `.codex/config.toml` is a **real, live config** Codex reads to launch MCP
servers. Writing an entry here is activation-adjacent, not documentation — so this
module uses the same stricter auto-wire bar as the Goose recipe extension path
(`mcp-emit.md`'s Goose section): **first-party, every tool read-only, no
`security_review.required`**. Anything else is skipped and surfaced, never silently
activated.

Opt-in via [`--target-host-features codex:mcp`](host-features.md).

## Why Goose's bar, not Claude's — checked against Codex's own baseline

Goose's stricter bar is justified relative to a specific fact: every Goose agent
defaults to the `developer` builtin extension (unconditional local shell, always on).
Borrowing that threshold for Codex without checking Codex's own baseline would be an
unverified analogy. Checked via live web search (2026-08-10): Codex's own default
posture is markedly *more* conservative than Goose's — `suggest` is the default
approval mode (every action requires explicit operator approval before execution),
and `sandbox_mode` defaults to no network access with filesystem writes confined to
the active workspace, applied as an independent runtime gate at tool-call time
regardless of `config.toml` contents. Writing a first-party/read-only server into
`[mcp_servers.*]` doesn't, by itself, grant any capability Codex's own default
sandbox/approval layer wouldn't still gate.

## No comment-preserving TOML round-trip — text-level splice instead

`.codex/config.toml` is shared and multi-purpose (sandbox/profile settings live
alongside `[mcp_servers.*]`); it cannot be blind-overwritten. stdlib `tomllib` is
read-only and drops comments/formatting on parse, and no TOML-writing dependency is
declared (this project's one runtime dependency is `jsonschema`), so this module
never parses-and-reserializes the whole file. It splices at the **text level**:
locate and remove any existing `[mcp_servers.*]` table blocks by line-scanning the
raw text (bounded by a `# --- BEGIN/END agentteams-managed mcp_servers ---` marker
pair, or a bare `[mcp_servers.*]` header from a hand-authored/pre-marker file), then
append freshly-rendered tables. Everything else — comments, unrelated tables,
formatting — is intended to survive unchanged. A content-preservation check
(parse both the pre- and post-splice text with `tomllib`, compare every key
outside `mcp_servers`) verifies this before any write; a mismatch — e.g. a
hand-authored multi-line string whose content merely looks like a table
header confusing the line scan — refuses the write entirely rather than risk
silent data loss. A single-generation `.bak` copy of the pre-existing file is
also written before any splice. This mirrors the project's own house style for
surgical text replacement ([`fences`](fences.md)'s fenced-region merge and
`yaml_frontmatter`'s line-anchored front-matter boundary scan) rather than a
general-purpose round-trip.

Re-running is idempotent: the managed block is replaced, not accumulated.

## Mapping (`mcp-server.schema.json` → Codex TOML)

| mcp-server.schema field | Codex `[mcp_servers.<id>]` field |
|---|---|
| `transport: stdio` + `command` + `args` | `command`, `args` |
| `transport: http` + `auth.url` | `url` |
| `auth.mechanism: env` + `credential_ref` (stdio) | `env_vars: [<credential_ref>]` (forwards a named host env var — distinct from `env`, which sets literal values) |
| `auth.mechanism: env` + `credential_ref` (http) | `bearer_token_env_var: <credential_ref>` |

`auth.mechanism` values `secret-store`/`oauth` are not expressible in this field set
and are skipped + surfaced (Codex's own `auth: oauth\|chatgpt` concept is a built-in
flow Codex manages itself, not the same as this schema's abstract mechanism enum).
`stdio` servers need `command`; `http` servers need `auth.url` — absent either, the
server is skipped + surfaced, never silently dropped.

## Public Surface

```python
@dataclass
class CodexMCPEmissionResult:
    written: list[str]
    errors: list[str]
    gated_off: bool
    wired: list[str]
    not_wired: dict[str, str]  # server_id -> reason
    dropped_unmanaged: list[str]  # pre-existing hand-authored server ids this run replaced
    success: bool  # property: len(errors) == 0
```

```python
codex_mcp_enabled(features: list[str]) -> bool
```
True iff the `codex:mcp` host-feature token is active.

```python
emit_codex_mcp_config(
    *,
    servers: list[dict],
    features: list[str],
    output_root: Path,
    dry_run: bool = False,
) -> CodexMCPEmissionResult
```
Splice wirable MCP servers into `output_root/.codex/config.toml`. No-op
(`gated_off=True`) when `codex:mcp` is not active; no-op when `servers` is empty.
Non-conforming entries (schema validation, via [`mcp_emit._inert_problems`](mcp-emit.md))
are routed to `result.errors`. An existing file that fails to parse as TOML refuses
the splice entirely (`result.errors`, file untouched) rather than guessing.

## Pipeline wiring

`cli.artifacts._emit_codex_mcp_if_enabled` mirrors `_emit_mcp_servers_if_enabled`'s
gate/best-effort shape and is called alongside it from
`cli.artifacts._emit_host_mcp_artifacts_if_enabled` — the one call site
`cli.generate`'s three build paths invoke, so adding a future host's MCP emitter
means touching `artifacts.py` once, not `generate.py`'s call sites again. Fires
independently of which `--framework` a given generate call renders — like the
Claude sidecar, this is a host-scoped artifact (which local tools read MCP config
from), not a per-framework rendering output.

## `toml_write`

The hand-rolled TOML table serializer this module renders through is documented
separately: [`toml_write`](toml-write.md).
