# `mcp_catalog` — AgentTeamsModule

The catalogue of foundational MCP servers every team can draw on, and the expansion that binds them to agents.
Each entry is one JSON file under `agentteams/templates/mcp/`. It is a valid `mcp-server.schema.json` definition
plus catalogue-only keys, which are removed before the entry reaches the manifest.

> Source: `agentteams/mcp_catalog.py`. Called from `cli/artifacts.finalize_privilege_wiring`, right after host
> features are resolved; `analyze.build_manifest` validates the brief's ids and adds the PR agents. Servers:
> [`mcp_servers`](mcp-servers.md). Background: `references/non-orchestrator-mcp.reference.md` §5b.

---

## The catalogue

| id | What it is | Default | Activation |
|---|---|---|---|
| `agentteams-recall` | First-party: `agentteams --serve-mcp recall` (memory and code index queries) | on where MCP is enabled | wired on Goose and Codex; inert file on Claude |
| `agentteams-gitread` | First-party: `agentteams --serve-mcp gitread` (isolated read-only git) | on where MCP is enabled | as above |
| `github-read` | GitHub's `github-mcp-server` v2.0.1, `--read-only --toolsets repos,issues,pull_requests` | opt-in | inert until the operator activates it after @security vetting |
| `github-write` | The same server limited by an exact `--tools` allowlist: reads plus branch, file, PR and issue writes. No merge, no file or repository deletion, no fork | opt-in | as above; making it live is Rule-15 constraint-relaxing |
| `fetch` | `mcp-server-fetch==2026.8.18` | opt-in | inert; activate only behind an egress proxy that blocks private and link-local addresses (upstream does not) |

Credentials are referenced by name only (`GITHUB_PERSONAL_ACCESS_TOKEN`), never inlined.

## Public surface

| Name | Purpose |
|---|---|
| `CATALOG_DIR` | `agentteams/templates/mcp/`. |
| `CATALOG_KEYS` | `catalog_default`, `role_scope`, `pin`: stripped before an entry reaches the manifest. |
| `GITHUB_IDS` | `github-read`, `github-write`: the ids that bring in the PR agents and are withheld under the switch. |
| `load_catalog() -> dict` | Every entry by `server_id`. Raises `ValueError` for an unreadable file or a `server_id` that does not match the file name. |
| `selection(description) -> (opt_ins, excludes)` | Validates the brief's `mcp_catalog` and `mcp_catalog_exclude`. Raises `ValueError` for a non-list or an unknown id. |
| `wants_pr_agents(description) -> bool` | True for `pr_management: true` or a GitHub opt-in; `pr-manager` and `pr-notifier` then join the roster. |
| `expand(manifest, framework_id) -> list[str]` | Adds the selected servers to `manifest["mcp_servers"]` after any declared ones and returns notices. |

## Expansion rules

1. The defaults apply when the framework's MCP token is set (`claude:mcp` or a bridge token, `goose:mcp`,
   `codex:mcp`). Then the brief's opt-ins are added and its exclusions removed.
2. **Codex** applies servers to the whole project (it ignores `scope`), so its defaults are withheld unless
   listed in `mcp_catalog`, with a notice.
3. **Under `write_policy: "orchestrator-only"`** nothing from the catalogue is emitted. The defaults are not
   needed (the orchestrator uses the CLI and git directly) and the GitHub servers wait for P5's signed grants.
4. Each server's `scope` is its `role_scope` intersected with the roster; with no match it is dropped.
5. A brief's own `mcp_servers` entry with the same `server_id` wins.

With no MCP token and no opt-in, the manifest is untouched.
