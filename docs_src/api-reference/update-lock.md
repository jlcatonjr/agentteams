# `update_lock` — Per-Checkout Update Lock and Run Record

> *Source: `agentteams/update_lock.py`*

Two self-updates of one checkout ran unrecorded on 2026-11-03, minutes apart, the first while a cleared
handoff was in flight. `build-log.json` keeps only the last run, so nothing showed that two had happened.
This module makes concurrent team writes visible: a second writer refuses instead of interleaving, and every
locked generate/`--update` run leaves one row in a run record.

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
- **Custody.** The git directory comes from [`gitdirs.git_dirs`](gitdirs.md), with the start path resolved first.
  A symlinked `.git`, `gitdir:` target or `commondir` target; a `gitdir:` target that holds no `HEAD`; a linked
  worktree whose `gitdir` back-pointer names another `.git` (a moved worktree: run `git worktree repair`); an
  oversized pointer file; or a symlinked lock file refuses the run (`UpdateLockError`, printed as
  `[UPDATE-LOCK] refused`, never a traceback). The record is opened `O_NOFOLLOW` and must be a regular file.
- **Lock-exempt runs.** `--dry-run`, and inspection-only runs (`--check`, `--list-backups`, `--scan-security`,
  `--check-budget`, `--check-rank[-strict]`, `--check-wiring`, `--query-index`, `--query-code`) that carry none of
  `--adopt-only`, `--adopt-orphans`, `--overwrite`, `--restore-backup`, `--refresh-index`, `--refresh-code-index`,
  `--reconcile-front-matter`, `--pin-templates`, take no lock and leave no record. When the lock is held they
  print a notice, since what they read may be mid-write. `--restore-backup` and `--pin-templates` are locked
  **even under `--dry-run`**: neither mode checks `--dry-run`, so both write (their dry-run behaviour is unchanged;
  they are only locked).
- **Unlocked with a notice.** Outside a git checkout; a git directory that can't be read; no `fcntl` (non-POSIX);
  or `EPERM`/`EACCES`/`EROFS` creating the lock (a sandbox). Unlocked runs leave no record. This fail-open is
  wider than "a sandboxed run can't write anyway": the sandboxed-write preflight runs only before the
  render/emit write phase, so the adopt step, `--adopt-only`, `--restore-backup` and `--pin-templates` still
  write, unlocked, wherever the sandbox allows; and a linked worktree whose git directory lies outside the
  sandbox's writable roots while its team directory is writable runs the whole pipeline unlocked.
- **Re-entrant per process.** A nested `update_run` in a process already inside one is a no-op, so an in-process
  re-entry of the pipeline never refuses itself and writes no second row.

**Not covered** (accepted residue; these write without the lock): `--bridge-from`/`--bridge-refresh`/`--bridge-merge`,
`--interop-from`, `--convert-from`, `--stale-remediate`, `--add-fence-markers`, `--sync`/`--sync-init`/`--sync-refresh-instructions` (dispatched
before the pipeline, through `multi_sync` and `sync_hub_refresh`), and the pre-commit hook's pipeline-graph refresh. The other standalone
commands that `cli/app.py` dispatches before the pipeline are unlocked too; those that write into a project include
`--stale-restore`, `--prune-backups`, `--refresh-graph`, `--refresh-architecture`, `--install-git-hooks`,
`--sync-project-rules`, `--apply-proposal`, `--set-vulnscan`, `--revert-migration`, `--absorb-from --absorb-apply`,
`--migrate-decision-log`, `--write-integrity-manifest`, `--capture-baseline`, `--package-team` and
`--goose-source`/`--goose-model`. `--migrate` creates its snapshot tag before the
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

The per-checkout git directory from [`gitdirs.git_dirs`](gitdirs.md), or `None` outside a checkout. Raises
`UpdateLockError` for any `GitDirError` (see *Custody*), `OSError` when `.git` can't be read.

### `known_options()`

Every option string the CLI parser defines (cached). `redact_argv` keeps only these.

### `redact_argv(argv, known)`

Flag names only, in order, and only option strings in `known` (default `known_options()`), so a token shaped like a
flag but unknown to the parser — an abbreviation, or a secret passed as `-AKIA…` — is dropped. `--framework`/`-f`,
`--shrink-policy` and `--adopt-only` keep their value when it is a slug of at most 200 characters
(`^[a-z0-9][a-z0-9,_-]{0,199}$`); every other value and positional is dropped.

### `detect_invoker(env)`

`claude-code`, `goose`, `codex` or `terminal`, from `INVOKER_ENV_MARKERS` (default `os.environ`).

### `run_mode(args, *, migrate_exemption)`

`migrate`, `adopt-only`, `restore-backup`, `pin-templates`, `update`, `overwrite` or `generate`.

### `is_lock_exempt(args)`

Whether the run is a dry run or inspection-only (see *Lock-exempt runs*).

### `agentteams_commit()`

The commit of the agentteams checkout this code runs from, read from git's files (no git spawn, every read
`O_NOFOLLOW` and size-capped; a ref outside `refs/` or containing `..` is not read); `unknown` for an installed
wheel or anything unreadable.

### `describe_holder(git_dir)`

One line naming the current holder from the holder record (printable characters only, truncated), or a
placeholder when it is missing or unreadable.

Exercised by `tests/test_update_lock.py`.
