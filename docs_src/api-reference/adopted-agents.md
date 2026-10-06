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

## Merge-mode adoption (`--update --adopt-orphans`)

> Source: `agentteams/cli/adopt_merge_gate.py` (enforcement module, pinned in
> `references/enforcement-integrity.json`) and `agentteams/cli/adopt_step.py` (pipeline glue).

Without `--overwrite`, adoption renders routing rows for every adoptable agent and extends the
orchestrator's front-matter `agents:` list **append-only**. `agents:` is a capability key: merge
never applies it automatically, because adding an entry widens what the orchestrator can dispatch
(C-3). This path is the one gated exception. Frameworks whose orchestrator has no `agents:` list
(Claude) get the routing rows only, and no gate applies to them.

| Condition | Rule |
|---|---|
| Workspace | Signing-governed only (`enforce_decision_signing`); refused otherwise |
| Clearance | A decision row, never a waiver, for action `adopt-orphans-merge` (an `overwrite` row does not satisfy it) |
| Scope | `scope` must be exactly `adopt-orphans-merge` |
| Exact grant | `effect_grants` must equal exactly `agents:<slug>` for each slug this run adds; a superset or subset refuses and names the difference |
| Signature | Declaring a grant makes Rule 15 require the operator's Ed25519 signature; `scope` and `effect_*` are inside the signed payload |
| Cap | The Rule 15(d) aggregate cap (`exception_registry.assert_within_aggregate_cap`) is checked first |
| Consumption | The clearance is spent once, on the real run; `--dry-run` never spends it |
| Write | The orchestrator is backed up, only the missing slugs are appended (sorted), nothing else in the file changes, and a re-run adds nothing |
| Record | Each applied run appends `applied_at, orchestrator, added, clearance_date, key_id, signature_prefix` to `references/adopt-orphans-merge.log.csv` |

The adoption is cleared and applied in Step 4, before render and emit, because an `--update` with
no material drift returns before emit. Routing rows that a plain `--update` carries forward from the
on-disk orchestrator are file content, not a clearance, so they never reach `agents:`.

Operator flow:

1. Run `agentteams --update --adopt-orphans --dry-run --json`. The `adopt_orphans_merge` object
   names the orchestrator, the exact `agents_to_add` and the clearance status.
2. Sign a decision with `action_reviewed=adopt-orphans-merge`, `scope=adopt-orphans-merge` and
   `effect_grants=agents:<a>;agents:<b>…` matching that list (`--sign-decision`).
3. Run `agentteams --update --adopt-orphans`.
