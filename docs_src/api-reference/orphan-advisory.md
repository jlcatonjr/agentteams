# `orphan_advisory` — AgentTeamsModule

The orphan-agent-file advisory printed by `--update`: agent files present in the agents
directory that the current run does not emit.

> Source: `agentteams/orphan_advisory.py`

---

Carved out of `build_team.py` (CH-07 ceiling). `build_team._report_orphan_agent_files` remains
as a shim that passes `build_team._persist_orphan_events` as the persister, so callers and tests
are unchanged. Detection only: nothing here deletes a file.

The glob suffix is the framework's own (`adapter.get_file_extension("agent")`), passed as the
required keyword-only `agent_ext`. Every candidate lands in one bucket, and every bucket is
printed:

| Bucket | Label | Persisted to the daily orphan-events log |
|---|---|---|
| `orphaned` | none, or "claims bridge-managed (unverified)" when the file's front matter (or a recipe's top-level keys) carries `bridge:` / `source_sha256:` | yes |
| `bespoke` | "bespoke roster member (no template): keep" — slug in `agent_slug_list` / `selected_archetypes` | no |
| `bridge` | "bridge-managed: keep" — a reserved bridge slug (`bridge_subagents_goose._RESERVED_SLUGS`, e.g. `bridge-orchestrator.yaml`) | no |

A front-matter claim is self-asserted, so it labels the file but never moves it out of the
orphaned bucket.

## Public surface

- `classify_orphan_agent_files(final_rendered, output_dir, manifest, *, agent_ext)` —
  returns `{ORPHANED: [...], BESPOKE: [...], BRIDGE: [...]}` of `(filename, label)` pairs.
- `report_orphan_agent_files(final_rendered, output_dir, manifest, *, agent_ext, persist=None)` —
  prints the advisory, calls `persist` with the orphaned filenames only, and returns them.
