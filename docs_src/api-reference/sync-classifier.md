# `sync-classifier` — AgentTeamsModule

Generalized three-way classifier for native↔canonical agent synchronization.

> Source: `agentteams/sync_classifier.py`

Generalizes `front_matter_merge._merge_front_matter`'s proven three-way decision table from
flat YAML keys to arbitrary CAI agent fields, and from the template↔deployed axis to the
canonical↔native axis. Classifies each field as unchanged / native-moved / canonical-moved /
both-moved-conflict, with a **capability-key carve-out** (§6.1) that always routes
capability-bearing fields to human review regardless of classification cleanliness.

---

## Public Constants

- `CAPABILITY_CAI_FIELDS` (`frozenset[str]`): CAI agent fields that carry capability grants
  (`capabilities`, `raw_front_matter`). These are the canonical-dict equivalents of
  `front_matter_merge.CAPABILITY_FRONT_MATTER_KEYS`.
- `AGENT_FIELDS` (`tuple[str, ...]`): ordered list of CAI agent fields the classifier examines
  (`name`, `description`, `body_markdown`, `capabilities`, `handoffs`,
  `invariant_core_markdown`, `source_path`, `raw_front_matter`).

---

## Public Enums

### `Classification`

Three-way classification of a single field:

- `UNCHANGED` — canonical and native agree
- `NATIVE_MOVED` — native edited, canonical unchanged since baseline
- `CANONICAL_MOVED` — canonical edited, native unchanged since baseline
- `BOTH_MOVED_CONFLICT` — both sides changed since baseline
- `NO_BASELINE` — no recorded baseline; nothing applied, report only

### `Action`

What the classifier recommends the caller do:

- `KEEP` — no change needed (or no-baseline: don't touch it)
- `APPLY` — safe to auto-apply (non-capability, clean provenance)
- `PROPOSAL` — route to human review; report but never auto-apply

---

## Public Dataclasses

### `FieldResult`

Classification of a single agent field. Fields: `field_name`, `classification`,
`action`, `canonical_value`, `native_value`, `baseline_value`, `notice`.

### `AgentSyncReport`

Complete classification report for one agent. Fields: `agent_slug`, `framework`,
`field_results`. Properties: `applied`, `proposals`, `unchanged`, `has_changes`,
`to_text()`.

### `SyncReport`

Top-level report covering all agents. Fields: `canonical_dir`, `native_dir`,
`framework`, `agent_reports`. Properties: `has_changes`, `total_proposals`,
`total_applied`, `to_text()`.

---

## Public Functions

### `is_capability_field(field_name, value) -> bool`

Check whether a CAI agent field is capability-bearing. Inspects `raw_front_matter`'s
contents to determine if it actually carries capability data (e.g. `tools`, `model`,
`disallowedTools` keys).

### `is_capability_field_any_side(field_name, canonical_value, native_value, baseline_value) -> bool`

The security-critical check the classifier actually calls (`_classify_field` calls
this, not `is_capability_field`, to enforce §6.1). Checks all three sides — not just
the native value — so a native-side removal of a capability key from
`raw_front_matter` isn't missed and misclassified as safe to auto-apply.

### `classify_agent(agent_slug, framework, canonical_agent, native_agent, baseline_agent) -> AgentSyncReport`

Classify all fields of a single agent. Handles agents present in canonical but not
native (or vice versa) by reporting them as proposals.

### `classify_sync(canonical_cai, native_cai, baseline, *, canonical_dir, native_dir, framework) -> SyncReport`

Top-level entry point. Indexes agents by slug in canonical, native, and baseline, then
classifies every field. Never writes anything to disk.
