# `manifest_format` — AgentTeamsModule

Manifest field-derivation and formatting helpers — the `_format_*` / `_default_*` / `_collect_*`
/ `_derive_*` cluster that turns description/manifest data into the rendered strings
`build_manifest` assembles.

> Source: `agentteams/manifest_format.py`

---

Carved from `analyze.py` (CH-07 line ceiling) and re-exported there, so `build_manifest`
(which stays in analyze) and external importers (`output_plan`, tests) resolve these from
`agentteams.analyze` unchanged.

## Examples of surface
- `_default_reference_db_path` / `_default_style_reference_path` / `_default_primary_output_dir`
  / `_default_output_format` — project-type defaults.
- `_format_authority_hierarchy` / `_format_agent_list` / `_format_domain_agent_list` /
  `_format_workstream_source_map` / `_format_unresolved_tool_list` — rendered manifest fields.
- `_collect_manual_required` (+ tool/component variants) — `{MANUAL:*}` placeholders requiring
  operator fill-in.
- `_dedupe_keep_order`, `_derive_diagram_tools` — shared helpers (re-imported by analyze).
