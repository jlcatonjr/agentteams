# `bridge_sources` — AgentTeamsModule

Source-team inventory extraction, source-file collection, SHA-256 hashing, and the
bridge-freshness check for the lightweight bridge. Framework-aware: markdown agent
files for claude/copilot sources, recipe YAML for a Goose source, and the exploded
canonical format (`agents/*.md` + `team.cai.json`, guarded by
`_require_canonical_team_file()`) for a canonical source.

> Source: `agentteams/bridge_sources.py`

---

Carved from `bridge.py` (CH-07 line ceiling) and re-exported there, so importers
resolve these helpers from `agentteams.bridge` unchanged.

## Inventory & hashing

- `_extract_inventory(source_dir, source_framework)` — one row per source agent;
  reads markdown front matter, a Goose recipe's `title:`/`description:` (and
  `sub_recipes:`/`prompt:` for invokability) when the source framework is goose, or
  canonical `agents/*.md` front matter (via `_load_agent_file`) when it is canonical.
- `_collect_source_files(source_dir, source_framework)` — the agent-definition files
  to hash: `.md` for claude/copilot, `.yaml` for a Goose source, or canonical
  `agents/*.md` plus `team.cai.json` for a canonical source — excluding build-tool
  artifacts (`_build-description.json`) and OS junk in every direction.
- `_compute_hash_rows(files, source_dir)` — `{path, sha256}` rows for the manifest.
- `_run_bridge_check(*, manifest_path, source_hash_rows)` — freshness verdict + report;
  fails a 0-inventory manifest (a wrong-source bridge cannot pass silently). Both
  arguments are keyword-only.
- `_render_inventory_md(rows, output_root=None)` — the `agent-inventory.md` compatibility
  table; when `output_root` is given, the source-file column is rendered relative to it
  instead of absolute.

## Verdict attribution

### `source_state_digest(source_hash_rows) -> str`

> *Source: `agentteams/bridge_sources.py`*

Digest the source state a bridge verdict was computed from.

Deterministic in the source tree alone: the same files always produce the same digest,
on any machine, at any time. That is what lets `bridge-check.report.md` be byte-stable
across re-runs **and** machine-comparable against the current tree — the two properties
a wall-clock timestamp cannot provide together.

**Args:**

- `source_hash_rows` (`list[dict[str, str]]`) — `{"path", "sha256"}` rows as recorded in
  the manifest. Sorted by path, so row ordering cannot perturb the digest.

**Returns:** `str` — Hex SHA-256 over the sorted `path:sha256` pairs.

**Why not a timestamp.** A committed check report is a *cached verdict*, and its defect
was never a missing date — it was that nothing detected the cache going stale. The
copilot-cli report sat at `PASS` for a week while six sources drifted. A first fix
recorded `Checked at: <wall clock>`, which conveyed staleness only to a human who
opened the file and did the arithmetic, and made a command documented *"read-only"*
rewrite a tracked file on every invocation. The digest fixes both:
`--bridge-check` now writes only when the bytes differ, and
`tests/test_bridge_mode_safety.py::test_committed_check_reports_describe_the_committed_source_state`
compares the **committed** digest against the working tree.

Same construction as `memory_index._documents_fingerprint`, this repository's existing
precedent for a path/hash fingerprint.
