# `front_matter_baseline`

The baseline the three-way front-matter merge compares against, and a guard that keeps a write from dropping a
project's hand-written handoffs.

> *Source: `agentteams/front_matter_baseline.py`* — integrity-pinned.

## Why it exists

The merge applies the template's value for a key when the on-disk value equals the baseline ("nobody has
touched it since generation"). Until 2026-10-11 `build-log.json` recorded the baseline as the front matter
**as written**. A merge writes the project's own value whenever it keeps it, so one run after a project's
edit was kept, the baseline held that edit, the disk matched it, and the next template change replaced it.
portal's orchestrator lost its two project-specific handoffs that way, with no notice.

## The baseline

`compute(written, template, previous)` records, per file and key, the value agentteams **authored**: the
written value when it equals the template's render, else the previous version-2 baseline's value, else
nothing. A key left out is unknown, so the merge keeps the project's value and proposes the template's.
`build_team._write_run_log` writes it with `front_matter_baseline_version: 2`.

`load(log)` reads the baseline from a parsed build log, and `load_for(output_dir)` from the agents
directory. A version-2 baseline is used whole. A legacy one (no version key) is unknown: any of its values
may be a kept project value, so the transition run keeps every project value and proposes the template's.
Legacy values are never carried forward.

`keep_disk_front_matter(content, existing_content)` puts the on-disk block back before the three-way merge
when the legacy whole-body migration wrote the render's front matter wholesale.

## The handoff guard

`restore_dropped_handoffs(new_content, existing_content, baseline)` runs on every `--merge` write (and in
the dry-run preview), whatever produced the merged content. `--overwrite`, interop and sync writes do not
pass through it. An on-disk `handoffs:` that is not a block
list, or content with no front matter, cannot be checked; that is reported with the same prefix (REVIEW).

A handoff is an (agent, label) pair. One the new block lacks is **dropped** when its agent is no longer
reached, or when the disk reached that agent more often than the new block does; otherwise it is a
**rewording**.

| On disk, missing from the new block | Outcome |
|---|---|
| Dropped, not in the trusted baseline | **restored** (indented to the new block), with a `front matter: kept hand-written handoff(s)` notice; fleet reports it as REVIEW (`handoffs_kept`) |
| Dropped, in the baseline (agentteams wrote it) | removed; the template retired it, and a notice says so |
| Rewording, not in the baseline | kept as the template's; a notice names the old wording |

| Name | Purpose |
|---|---|
| `VERSION_KEY` | `"front_matter_baseline_version"`. |
| `BASELINE_VERSION` | `2`. |
| `HANDOFF_KEPT_PREFIX` | `"front matter: kept hand-written handoff"`. |
| `load(log)`, `load_for(output_dir, *, version_2_only=False)` | The trusted baseline. |
| `keep_disk_front_matter(content, existing_content)` | The on-disk front matter on merged content. |
| `compute(written, template, previous)` | The version-2 baseline. |
| `handoff_items(value)` | `[((agent, label), item lines), ...]` from a block-style `handoffs:` value. |
| `restore_dropped_handoffs(new_content, existing_content, baseline)` | `(content, notices)`. |
