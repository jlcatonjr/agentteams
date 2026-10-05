# `adopted_agents` — AgentTeamsModule

Routing-table rows for agents registered with `--adopt-orphans`, so every framework's
orchestrator body routes to them.

> Source: `agentteams/adopted_agents.py`

---

`--adopt-orphans` adds pre-existing ("bespoke") agent files to the roster. On its own the roster
reached only the Copilot orchestrator's `agents:`/`handoffs:` front matter, which the Claude adapter
strips, so a Claude orchestrator never routed to an adopted agent. This module fills the
`{ADOPTED_AGENT_ROUTING_ROWS}` placeholder at the end of the orchestrator template's
`routing_table_rows` fence with one row per adopted agent, on every framework.

| Behaviour | Rule |
|---|---|
| No adopted agents | Placeholder is `""`: the routing table is byte-identical to before |
| Order | Sorted by slug, de-duplicated: byte-stable across runs and frameworks |
| Trigger text | The adopted file's `description:` only, treated as data (C-4): one line; `<`, `>`, backticks and double quotes removed; `\|` → `/`; at most 200 characters |
| Display name | `name:` (or a recipe's `title:`) minus any ` — <team>` suffix |
| No description | Row kept, with "(no description; add a description: to the agent file)" |
| Provenance | A `<!-- X-export: … -->` comment leading the body marks the row "maintained upstream in X — do not hand-edit" |
| Later `--update` without `--adopt-orphans` | Rows already in the on-disk orchestrator are carried forward while their agent file exists; `--prune` stays the removal path |
| Non-agent files | Not adopted: a `.md` without front matter (e.g. `SETUP-REQUIRED.md`) or a `.yaml` that is not a `version: "1.0.0"` recipe |

Only files already in the output directory being generated are read, never fetched or
unmerged content.

## Public surface

- `read_adopted_agent_metadata(path)` — `{name, description, upstream}` from a `.md`, Goose `.yaml` or Codex `.toml` agent.
- `is_agent_file(path)` — whether a file in the agent directory is an adoptable agent definition.
- `format_adopted_routing_rows(slugs, metadata=None, *, agent_dir="", agent_ext="")` — the placeholder value.
- `adoption_exclusions(manifest)` — slugs never adopted: files this build emits, and legacy `tool-*` agents.
- `discover_orphans(output_dir, agent_ext, exclude)` — `(sorted slugs, {slug: metadata})`.
- `previously_adopted(output_dir, agent_ext)` — slugs an earlier adopt run rendered into the on-disk orchestrator.
- `agent_dir_label(output_dir, project_root)` — repository-relative agent directory cited in each row.

`analyze.adopt_orphan_agents(manifest, orphan_slugs, metadata=None, *, agent_dir="", agent_ext="")`
calls `format_adopted_routing_rows` and stores the result under
`auto_resolved_placeholders["ADOPTED_AGENT_ROUTING_ROWS"]`.
