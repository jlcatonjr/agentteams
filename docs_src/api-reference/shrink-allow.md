# `shrink_allow` — AgentTeamsModule

Reviewed, content-bound, per-section overrides of the shrink guard (`--shrink-allow`,
`AGENTTEAMS_SHRINK_ALLOW`), and the review report (`AGENTTEAMS_SHRINK_REPORT`). See the
[CLI reference](../cli-reference.md).

> *Source: `agentteams/shrink_allow.py`*

An entry is `<rel path>:<fence id>@<digest>`, where the digest is the first 12 hex of the sha256
of the reviewed on-disk body. It releases that section only while the body still matches. A
released section takes the replace path in `fences._merge_fenced_content`, and its old body goes
to the `.lost` sidecar. `announce` refuses every override on a run without a backup dir.

| Name | Purpose |
|---|---|
| `parse_entries`, `resolve` | Validate entries from flags and `AGENTTEAMS_SHRINK_ALLOW`. Raises `ShrinkAllowError`. |
| `body_digest(block)`, `key(rel_path, sid, block)` | The digest and the exact entry for an on-disk section. |
| `announce(allowed, has_backup=, dry_run=)` | Print the active entries; refuse all of them without a backup dir. |
| `track(used, report, rel_path, existing, merge_result)` | Record the entries that fired and the sections still pinned. |
| `warn_unused(allowed, used)` | Warn about entries that released nothing. |
| `token_provenance(token, checkout)` | Classify a token as `retired`, `current`, `never` or `unknown` against agentteams' template git history. |
| `Report` | Collect pinned sections and write the JSON + Markdown review when `AGENTTEAMS_SHRINK_REPORT` is set. |
