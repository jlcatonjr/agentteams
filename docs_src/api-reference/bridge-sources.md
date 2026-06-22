# `bridge_sources` — AgentTeamsModule

Source-team inventory extraction, source-file collection, SHA-256 hashing, and the
bridge-freshness check for the lightweight bridge. Framework-aware: markdown agent
files for claude/copilot sources, recipe YAML for a Goose source.

> Source: `agentteams/bridge_sources.py`

---

Carved from `bridge.py` (CH-07 line ceiling) and re-exported there, so importers
resolve these helpers from `agentteams.bridge` unchanged.

## Inventory & hashing

- `_extract_inventory(source_dir, source_framework)` — one row per source agent;
  reads markdown front matter, or a Goose recipe's `title:`/`description:` (and
  `sub_recipes:`/`prompt:` for invokability) when the source framework is goose.
- `_collect_source_files(source_dir, source_framework)` — the agent-definition files
  to hash: `.md` for claude/copilot, `.yaml` for a Goose source — excluding build-tool
  artifacts (`_build-description.json`) and OS junk in every direction.
- `_compute_hash_rows(files, source_dir)` — `{path, sha256}` rows for the manifest.
- `_run_bridge_check(manifest_path, source_hash_rows)` — freshness verdict + report;
  fails a 0-inventory manifest (a wrong-source bridge cannot pass silently).
- `_render_inventory_md(rows)` — the `agent-inventory.md` compatibility table.
