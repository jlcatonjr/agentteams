# `canonical` — AgentTeamsModule

Durable exploded on-disk canonical agent format (durable-canonical-agent-format plan §5.5).

> Source: `agentteams/canonical.py`

Expands the Canonical Agent Interface (CAI) dict produced by `interop.export_to_cai` into a
human-editable directory form, and reads it back losslessly.

---

## Public Constants

- `TEAM_FILE_NAME` (`"team.cai.json"`): the format's identity marker; `detect_framework`
  recognition of a canonical directory keys on its presence (Phase F).
- `DEFAULT_CANONICAL_SUBDIR` (`".agentteams/canonical"`): default location convention under a
  project root, reusing the established `.agentteams/` control-directory convention
  (`.agentteams/brief.json` is the existing precedent).

---

## Public Types

### `CanonicalMaterializeResult`

Summary of a materialization run.

Fields:

1. `written`: paths (relative to the output dir) written — or planned, under dry-run.
2. `dry_run`: whether the run was simulated.

Property:

- `success`: `True` (validation errors raise before any write).

---

## Public Functions

### `materialize_canonical(cai, out_dir, *, dry_run=False)`

Writes the exploded directory form:

1. `team.cai.json` — project-level data: `schema_version`, `created_at`, source metadata
   (`source_framework`, `source_dir`), `instructions_binding`, `mcp_servers`,
   `framework_extensions`, and any other top-level keys the CAI dict carries (forward-compatible
   pass-through). Agents, skills and references are NOT duplicated here — they explode to files.
2. `agents/<slug>.md` — YAML front matter (`slug`, `name`, `description`, `source_path`,
   `capabilities` with the canonical tool-scope vocabulary, `handoffs`, optional
   `raw_front_matter`) plus the body markdown; a captured invariant-core fenced span is appended
   to the body (same convention as `interop.import_from_cai`).
3. `skills/<slug>/SKILL.md` plus co-located files, written verbatim (byte-faithful, so captured
   front matter survives unchanged).
4. `references/**` — non-agent reference content from the CAI `references` list, restored at its
   recorded `rel_path`.

All writes go through `atomicio._atomic_write_text`; `dry_run=True` collects the planned writes
with zero filesystem side effects (not even `mkdir`). Raises `ValueError` when the CAI dict fails
schema validation (`_validate_cai`, checked before any write) or contains other malformed input
(missing/invalid agents, unsafe slugs, escaping reference paths) so bad input never half-writes.

### `load_canonical(dir_path)`

The inverse: reads the exploded directory back into exactly the shape `interop.export_to_cai`
emits. `team.cai.json` supplies project-level fields; agents are reassembled from
`agents/*.md` (the invariant-core span re-lifted with the same capture helper so the export
shape is reproduced exactly); skills from `skills/*/SKILL.md`; references from the
`references/` tree when present. Agents and skills are sorted by slug. Raises
`FileNotFoundError` when `team.cai.json` is absent, and `ValueError` when the reassembled CAI
dict fails schema validation (`_validate_cai`, called after reassembly).

---

## Serialization Contract

Front matter is Markdown + YAML deliberately: it diffs and hand-edits far better than JSON and
matches every framework's file style except Goose. Every string value is emitted as a JSON
double-quoted scalar (a strict YAML subset), so loading needs no PyYAML — a test-only
dependency. `load_canonical` prefers `yaml.safe_load` when importable (hand-edit tolerance) and
falls back to a built-in parser covering exactly the subset this module emits.

Slugs are restricted to `[A-Za-z0-9][A-Za-z0-9._-]*` and reference paths refuse absolute paths
or `..` segments, keeping materialization traversal-proof.

---

## Round-trip guarantee

`load_canonical(materialize_canonical(export_to_cai(src)))` reproduces the export dict exactly
for every supported source framework (verified for copilot-vscode including all handoffs, and
for claude including skills and invariant-core spans).
