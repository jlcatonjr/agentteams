# `session_scan`

Deterministic repo at-large issue scan for the orchestrator's Workflow 11 Part B ("Repo At-Large
Issues") closeout gate. Consolidates three of the four issue sources that gate step describes in
prose — `CHANGELOG.md` "Known Issues", pending/blocked rows in the gitignored plan-steps tree,
`git status --short` anomalies — into one function returning structured findings, instead of three
independently hand-run greps.

The fourth source, `{CONFLICT_LOG_PATH}`, is intentionally **not** covered here:
`orchestrator.template.md` step 2 already routes it through `@conflict-resolution`'s
ACCEPT/REJECT/REVISE decision — a judgment call, not a summarize-and-present job — and folding it
into a generic scanner would regress that.

> *Source: `agentteams/session_scan.py`*

## Layout

- **Module:** `agentteams.session_scan` (importable)
- **CLI:** `python -m agentteams.session_scan [repo_root]`

## Public Surface

### `RepoIssue`

> *Source: `agentteams/session_scan.py`*

One repo at-large issue surfaced by `scan_repo_issues()`.

**Attributes:**

- `source` (`str`) — One of `"changelog"`, `"steps_csv"`, `"git_status"`.
- `path` (`str`) — Repo-relative path the issue was found in.
- `detail` (`str`) — One-line human-readable summary of the issue.

### `scan_repo_issues(repo_root, *, exclude_steps_paths=None, known_output_paths=None, runner=_run_git)`

> *Source: `agentteams/session_scan.py`*

Return the repo at-large issues from the three still-manual sources, in source order (changelog,
then steps.csv, then git status).

**Args:**

- `repo_root` (`Path`) — Repository root to scan.
- `exclude_steps_paths` (`set[Path] | None`, keyword-only) — `.steps.csv` files to skip — pass the
  current plan's own steps file so it isn't reported back to itself.
- `known_output_paths` (`set[str] | None`, keyword-only) — Repo-relative paths the current plan
  declares as its own outputs — a modified file in this set is not flagged by the `git_status`
  source.
- `runner` (`GitRunner`, keyword-only) — Injectable `git` subprocess runner (mirrors
  `pr_management.py`'s `_run_gh`/`GhRunner` testability shape). Default: shells out to `git`.

**Returns:** `list[RepoIssue]`

**Source scanning notes:**

- **`CHANGELOG.md`** — finds a heading matching `Known Issues` (case-insensitive, any `#` level)
  and collects its bullet lines, skipping any bullet already wrapped in `~~strikethrough~~`
  (already marked resolved at that same site).
- **Plan-steps tree** — globs `tmp/by-week/**/*.steps.csv` and legacy `tmp/*.steps.csv` (both
  gitignored — absent on a fresh clone is a valid empty result, not an error), reads each with
  `agentteams.plan_steps.read_steps()`, and reports rows whose `status` is `pending` or `blocked`.
- **`git status --short`** — reports untracked files under the gitignored `tmp/` tree, and any
  modified file not in `known_output_paths`. Paths are always repo-relative, never absolute.

## CLI

```bash
# Scan the current repo, print a JSON list of RepoIssue records
python -m agentteams.session_scan

# Scan a specific repo root
python -m agentteams.session_scan /path/to/repo
```

Exit code is always `0` — this is a report, not a pass/fail gate (unlike `agentteams.scan`'s
`python -m` entrypoint, which exits `1` on a HALT-worthy finding).

## Usage from a template

`orchestrator.template.md` Workflow 11 Part B step 1 cites this function directly, following the
same invoke-if-available convention used for [`handoff_payloads`](handoff_payloads.md) and
[`behavioral_drift`](behavioral-drift.md) — no CLI flag or skill file is required; a template
documents the dotted path and the agent invokes it via its own code-execution tool when available,
falling back to re-deriving each source by hand otherwise.

## See Also

- [`plan_steps`](plan_steps.md) — the CSV reader this module's steps-tree scan reuses.
- [`scan`](scan.md) — the deterministic security scanner, a sibling "code as verification
  interface" utility with its own `python -m` entrypoint.
