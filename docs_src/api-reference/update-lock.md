# `update_lock` — Per-Checkout Update Lock and Run Record

> *Source: `agentteams/update_lock.py`*

Two self-updates of one checkout ran unrecorded on 2026-11-03, minutes apart, the first while a cleared
handoff was in flight. `build-log.json` keeps only the last run, so nothing showed that two had happened.
This module makes concurrent team writes visible: a second writer refuses instead of interleaving, and every
writing run leaves one row in a run record.

[`cli/generate.py`](cli.md) wraps everything after `resolve_output_dir` in `update_run`:
the adopt step, the standalone modes (`--adopt-only`, `--restore-backup`, `--refresh-index`, …), the write
phase and post-build. The pipeline is split into a pre-lock half (ingest, validate, analyze, resolve the
output directory — nothing written) and a locked half, so every early return of the locked half passes
through `set_exit`.

## Behaviour

- **Where.** Both files live in the *per-checkout* git directory, found without spawning git by walking up
  from the project root to `.git` (a directory, or a linked worktree's `gitdir:` file):
  `<git-dir>/agentteams-update.lock` and `<git-dir>/agentteams-update-runs.log.csv`. The git directory is never
  tracked, so consumer trees gain nothing. One checkout means one lock, which serialises the Claude and Copilot
  runs that share project-level files such as `.vscode/tasks.json`; separate worktrees stay independent.
- **Lock.** `flock(LOCK_EX | LOCK_NB)` on a lock file opened `O_CREAT | O_NOFOLLOW`, mode 0600. The OS releases
  it when the holding process dies, so nothing goes stale. A held lock refuses the run before anything is
  written (`[UPDATE-LOCK] refused: …`, exit 1), naming the holder's pid, invoker, start time and redacted argv.
- **Holder record.** `<git-dir>/agentteams-update.holder.json` (`{pid, started_at, invoker, argv_redacted}`),
  written to a temp file and renamed over, so a reader never sees half of it. It is a separate file because
  renaming over the lock file would swap the inode `flock` holds. It is removed before the lock is released.
- **Run record.** One CSV row per locked run, appended while the lock is held: `started_at`, `finished_at`,
  `pid`, `invoker`, `mode`, `framework`, `argv`, `agentteams_commit`, `exit`. `exit` is the run's exit code,
  `exception` when it raised, or `unset`. A record that can't be appended (or is a symlink) prints a warning and
  never changes the exit code.
- **Custody.** A symlinked `.git`, a `gitdir:` naming a symlink, or a symlinked lock file refuses the run
  (`UpdateLockError`). The record is opened `O_NOFOLLOW` and must be a regular file.
- **Lock-exempt runs.** `--dry-run`, and inspection-only runs (`--check`, `--list-backups`, `--scan-security`,
  `--check-budget`, `--check-rank[-strict]`, `--check-wiring`, `--query-index`, `--query-code`) that carry none of
  `--adopt-only`, `--adopt-orphans`, `--overwrite`, `--restore-backup`, `--refresh-index`, `--refresh-code-index`,
  `--reconcile-front-matter`, take no lock and leave no record. When the lock is held they print a notice, since
  what they read may be mid-write.
- **Unlocked with a notice.** Outside a git checkout; a git directory that can't be read; no `fcntl` (non-POSIX);
  or `EPERM`/`EACCES`/`EROFS` creating the lock (a sandbox — the sandboxed-write preflight then refuses the real
  write anyway). Unlocked runs leave no record.
- **Re-entrant per process.** A nested `update_run` in a process already inside one is a no-op, so an in-process
  re-entry of the pipeline never refuses itself and writes no second row.

**Not covered** (accepted residue; these write without the lock): `--bridge-from`/`--bridge-refresh`/`--bridge-merge`,
`--interop-from`, `--convert-from`, `--stale-remediate`, `--add-fence-markers`, `--sync`/`--sync-init` (dispatched
before the pipeline, through `multi_sync`), and the pre-commit hook's pipeline-graph refresh. The other standalone
commands that `cli/app.py` dispatches before the pipeline are unlocked too; those that write into a project include
`--stale-restore`, `--prune-backups`, `--refresh-graph`, `--refresh-architecture`, `--install-git-hooks`,
`--sync-project-rules`, `--apply-proposal` and `--set-vulnscan`. `--migrate` creates its snapshot tag before the
lock; its delegated `--overwrite` is locked. `--fleet` locks each workspace's run separately.

## Public constants

- `LOCK_NAME`, `HOLDER_NAME`, `RECORD_NAME`: the three file names in the git directory.
- `RECORD_FIELDS`: the run record's header, in column order.
- `INVOKER_ENV_MARKERS`: `(variable, invoker)` pairs — `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT` → `claude-code`, `GOOSE_SESSION_ID` / `GOOSE_TERMINAL` → `goose`,
  `CODEX_SANDBOX` / `CODEX_SANDBOX_NETWORK_DISABLED` → `codex`; none → `terminal`. Advisory: an environment can
  be forged.

## Public classes

### `UpdateLockError`

A `RuntimeError`: the lock location failed a custody check; the run is refused.

### `UpdateLocked`

An `UpdateLockError`: another process holds the lock for this checkout.

### `RunHandle`

Yielded by `update_run`. `set_exit(rc)` records the run's exit code; `exit_code` holds it (`None` until set).

## Public functions

### `update_run(project_root, argv, *, dry_run, mode, framework)`

Context manager: hold the lock around one run and append its record on exit.

**Args:**

- `project_root` (`Path`) — Any path inside the checkout (need not exist yet).
- `argv` (`Sequence[str]`) — The run's arguments, recorded through `redact_argv`.
- `dry_run` (`bool`) — The run writes nothing: no lock, no record, a notice when the lock is held.
- `mode` (`str`, default `"generate"`) — The record's `mode` (see `run_mode`).
- `framework` (`str`, default `""`) — The record's `framework`.

**Yields:** `RunHandle`.

**Raises:** `UpdateLocked` before the body runs when the lock is held; `UpdateLockError` on a custody failure.

### `find_git_dir(start)`

Walk up from `start` to the per-checkout git directory (following a `gitdir:` file). Returns `None` outside a
checkout. Raises `UpdateLockError` for a symlinked or malformed `.git`, `OSError` when `.git` can't be read.

### `redact_argv(argv)`

Flag names only, in order. `--framework`/`-f`, `--shrink-policy` and `--adopt-only` keep their value when it is
a slug (`[a-z0-9][a-z0-9,_-]*`); every other value and positional is dropped.

### `detect_invoker(env)`

`claude-code`, `goose`, `codex` or `terminal`, from `INVOKER_ENV_MARKERS` (default `os.environ`).

### `run_mode(args, *, migrate_exemption)`

`migrate`, `adopt-only`, `restore-backup`, `update`, `overwrite` or `generate`.

### `is_lock_exempt(args)`

Whether the run is a dry run or inspection-only (see *Lock-exempt runs*).

### `agentteams_commit()`

The commit of the agentteams checkout this code runs from, read from git's files (no git spawn); `unknown` for an
installed wheel.

### `describe_holder(git_dir)`

One line naming the current holder from the holder record (printable characters only, truncated), or a
placeholder when it is missing or unreadable.

Exercised by `tests/test_update_lock.py`.
