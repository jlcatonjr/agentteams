# `fences` — AgentTeamsModule

Section-fencing internals: the `AGENTTEAMS:BEGIN/END` regexes, `MergeResult`, fenced-region
extraction, the fenced-content merge, shrink detection, and lost-fence sidecars.

> Source: `agentteams/fences.py`

---

Carved from `emit.py` (CH-07 line ceiling) and re-exported there, so `drift`, `fence_inject`,
and tests resolve these symbols from `agentteams.emit` unchanged.

## Key surface
- `MergeResult` — outcome of a single fenced-content merge (replaced/added/orphaned/preserved
  sections, parse errors, shrink notices, lost-fence bodies).
- `_extract_fenced_regions(content)` — map of `section_id → fenced body` (or an error string).
- `_merge_fenced_content(...)` — re-render only the template-owned fenced regions, preserving
  user content outside fences; honors the shrink policy.
- `_detect_fence_shrink(...)` — heuristic that flags a regenerated fence body as materially
  shorter / less specific than the on-disk body (list-item / path / backtick-ident loss).
- `_write_lost_fence_sidecars(...)` — persist pre-merge bodies of shrunk fences for recovery.
