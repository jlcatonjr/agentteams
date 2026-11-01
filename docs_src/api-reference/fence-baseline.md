# `fence_baseline`

Which fence bodies agentteams authored, so a fence nobody edited takes a template update instead of being
kept by the shrink guard.

> *Source: `agentteams/fence_baseline.py`*

## Why it exists

Under the default `--shrink-policy=preserve`, `--update --merge` keeps a fence's on-disk body whenever the
new render would shrink it (`fences._detect_fence_shrink`). That guard protects enrichment. It cannot tell
enrichment from an older template's own text. On 2026-11-01 ops-team's `vuln-remediation` agent was still
running its 2026-10-23 `daily_remediation` workflow, byte-identical to that day's render. Every later update
had preserved it because the old body backticked `failed` and `refused` and the new one discusses them in
prose ("lost concrete refs").

## The baseline

`build-log.json` key `fence_baseline`: `{rel_path: {section_id: sha256}}`, written by
`build_team._write_run_log`. The digest covers the whole fenced block, BEGIN and END markers included.
`compute(written_abs_paths, output_dir, template, previous)` records a fence's digest when the block written
this run equals this run's template render, or carries the previous record forward. Otherwise it records
nothing. It never records what was written: a merge writes a preserved hand edit back to disk, and recording
it would let the next run overwrite it (the `front_matter_baseline` lesson).

## The release

When a merge would shrink-preserve a fence and its on-disk block's digest equals the recorded one, the fence
is replaced (`MergeResult.shrink_unedited`). Its old body goes to the `.lost` sidecar, and the notice ends
`replaced: unedited since generation`. The dry-run plan reads `replaced` too. Otherwise behaviour is
unchanged: an edited fence, a fence with no record (any pre-2026-11-01 build log), or an interop projection
marker keeps the on-disk body. A run with no backup dir (`--no-backup`) also keeps it, since nothing would
save the old body. Template-authoritative fences, `--shrink-allow` and `--shrink-policy=halt` work as before.

| Name | Purpose |
|---|---|
| `BUILD_LOG_KEY` | `"fence_baseline"`. |
| `digest(block)` | Full SHA-256 hex of one fenced block. |
| `block_digests(content)` | `{section_id: digest}` for every fence in a file. |
| `load(log)`, `load_for(output_dir)` | The recorded baseline; `{}` when absent, malformed or an interop marker. |
| `for_run(output_dir, *, merge, dry_run, twin_drop, backup_path)` | The baseline an `emit_all` run may release with; `{}` without a backup dir. |
| `is_unedited(file_baseline, sid, block)` | Whether an on-disk block matches its recorded digest. |
| `compute(written_abs_paths, output_dir, template, previous)` | The authored baseline for the build log. |
