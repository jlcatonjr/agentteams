# `whole_body_migration` — AgentTeamsModule

Retires a legacy whole-body `content` fence once its template has been split into named sections, and
reports a section that appears twice in a merged file. [`fences`](fences.md) calls it from
`_merge_fenced_content` and `_detect_duplicate_sections`.

> *Source: `agentteams/whole_body_migration.py`* — integrity-pinned.

## Why it exists

A template with no fences is wrapped in one `content` fence at emit time. When the template gains named
sections, the render stops being wrapped. Until 2026-10-11 the merge retired the stale `content` block only
when it was the file's only fence. Files that also carried `memory_index_consultation` got the named
sections added beside it, so every section appeared twice, both copies inside fences (quality-auditor,
tool-doc-researcher and conflict-resolution across 12 repositories, in `.github/agents` and Goose recipes).

## The migration

`applies(existing, new)` is true when `content` is on disk, absent from a render that has named sections,
and the only on-disk fence the render no longer has. A file with a second orphaned fence is not migrated;
the duplicate report names its problem instead.

`migrate(...)` rewrites only the `content` block, then `fences` runs the ordinary merge on the result. The
other fences update in place, the front matter goes through the three-way merge, and the twin pass runs.
The block is replaced by the render's body (its first line to its last fence) with the fences the file
already carries cut out. Text outside the block is untouched.

Each heading section of the old body is matched to the render:

| Old section | Outcome |
|---|---|
| Every line survives in the result | dropped |
| Heading the render keeps unfenced | the deployed copy is kept unless it is provably template text |
| A user region, or a heading the render does not have | carried out of the fence, unfenced |
| Equals a past template rendering of its heading (lineage corpus) | dropped |
| An unproven copy of a section the render fences | **not kept**: keeping it recreates the duplicate |
| Holds a managed marker or a TOML string delimiter | **not kept**: a marker outside its fence would forge one |

`migration_notice` names every kept, carried, dropped-as-retired and not-kept section. The notice is a
`fence 'content':` shrink notice, so the old body goes to the `.lost.content.md` sidecar (also under
`--shrink-policy allow`) and `fleet` reports REVIEW.

## The duplicate report

`duplicate_notices(merged, added_sids)` reports each `##`–`######` heading that appears more than once,
saying where the copies sit. Each notice starts with `DUPLICATE_PREFIX` (`duplicate section`), which
`fleet` files as its own REVIEW class (`duplicates`), not as a shrink notice.

| Copies | Reported |
|---|---|
| One inside a fence, one outside | on every merge |
| All inside fences, spread over two or more fences | on every merge |
| All outside fences | only when the merge added a fence |
| Repeated inside one fence | never (the template's own structure) |

| Name | Purpose |
|---|---|
| `WHOLE_BODY_FENCE` | `"content"`. |
| `DUPLICATE_PREFIX` | `"duplicate section"`. |
| `Migration` | The rewritten file and what happened to each old section. |
| `applies(existing_regions, new_regions)` | The predicate above. |
| `migrate(new_rendered, existing, existing_regions, *, bridge_blocks=None, twin_source=None)` | Returns a `Migration`, or `None` when the layout cannot be mapped (the render's indentation differs, or it has no fence). |
| `migration_notice(mig, *, has_tail)` | The notice line. |
| `duplicate_notices(merged, added_sids)` | The duplicate report. |
