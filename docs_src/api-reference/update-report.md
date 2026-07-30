# `update_report` — Durable Record of an `--update` Run

> *Source: `agentteams/update_report.py`*

`--fleet`, `--bridge-merge` and `--recipe-check` all leave a report file. `--update`,
the most common operation, left none: preserved fences, skipped legacy files and
retrofitted markers went to stdout and died with the scrollback.

This module writes `update.report.md` beside the artifacts the run acted on, following
the P-0 report convention (`<operation>.report.md`, `## subject` headings, `- PASS` /
`- FAIL:` bullets).

## Two properties that are load-bearing

**Silent on a clean run.** A report file that appears every time trains its reader to
ignore it. `has_attributable_events` gates the write: nothing to attribute, nothing
written.

**Never affects an exit code.** The report is a record, not a gate. A failure to write
it must not turn a successful update into a failed one — the update already happened,
and the artifacts on disk are the truth.

## API

### `has_attributable_events(result) -> bool`

Whether the run made any decision worth recording — a preserved fence, a skipped
legacy file, a retrofitted marker, a shrink notice.

**Args:**

- `result` — The emit result for the run.

**Returns:** `bool` — `False` on a clean run, in which case no report is written.

### `build_report(result, *, backup_path) -> str`

Render the report body. Pure — no I/O.

**Args:**

- `result` — The emit result for the run.
- `backup_path` (`Path | None`, keyword-only) — Backup directory for this run, cited in
  the report so a reader can recover a preserved or lost body. `None` when
  `--no-backup` was passed.

**Returns:** `str` — The report markdown.

### `write_report(result, output_dir, *, backup_path) -> Path | None`

Write the report if there is anything to report.

**Returns:** `Path | None` — The written path, or `None` when the run was clean.

### `report_run(result, output_dir, backup_path) -> None`

The single entry point the CLI calls. Wraps `write_report` and absorbs any write
failure, because a reporting failure must not change the outcome of a completed
update.

Invoked from `agentteams/cli/generate.py` after the write phase, and only when
`--dry-run` was **not** passed — a dry run makes no decisions to attribute.

## Constants

- `REPORT_NAME` — `"update.report.md"`.

Exercised by `tests/test_update_report.py`.
