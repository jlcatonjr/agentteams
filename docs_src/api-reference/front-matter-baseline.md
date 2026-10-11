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

`load(log)` reads the baseline from a parsed build log. A version-2 baseline is used whole. A legacy one
(no version key) loses its block-valued keys (`handoffs:`) and its capability keys (`tools:`, `agents:`,
`model:` ...), where projects author entries; its scalar metadata keys keep their old trust for the one
transition run. Legacy values are never carried forward.

## The handoff guard

`restore_dropped_handoffs(new_content, existing_content, baseline)` runs on every merge write (and in the
dry-run preview), whatever produced the content. A handoff is identified by its target agent.

| On disk, missing from the new block | Outcome |
|---|---|
| Target the new block no longer reaches; not in the trusted baseline | **restored**, with a `front matter: kept hand-written handoff(s)` notice; fleet reports it as REVIEW (`handoffs_kept`) |
| Target not reached, but the baseline shows agentteams wrote it | removed; the template retired it, and a notice says so |
| Target still reached, with the template's label/prompt | kept as the template's; a notice names the old wording |

| Name | Purpose |
|---|---|
| `VERSION_KEY` | `"front_matter_baseline_version"`. |
| `BASELINE_VERSION` | `2`. |
| `HANDOFF_KEPT_PREFIX` | `"front matter: kept hand-written handoff"`. |
| `load(log)` | The trusted baseline. |
| `compute(written, template, previous)` | The version-2 baseline. |
| `handoff_items(value)` | `[((agent, label), item lines), ...]` from a block-style `handoffs:` value. |
| `restore_dropped_handoffs(new_content, existing_content, baseline)` | `(content, notices)`. |
