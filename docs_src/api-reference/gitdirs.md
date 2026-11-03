# `gitdirs` — Find a Checkout's Git Directories Without Spawning Git

> *Source: `agentteams/gitdirs.py`*

One place for "which `.git` does this path belong to". [`update_lock`](update-lock.md) uses it to place the
per-checkout lock and run record. `vulnscan/registry.py`, `vulnscan/subproc_confine.py` and `git_hooks.py` still
carry their own lookups.

## Rules

- **Walk.** From the *resolved* start path upward to the first `.git`. A directory is the git directory; a file
  is a linked worktree (or submodule) whose `gitdir:` line names it, relative to the file's directory when not
  absolute. The common directory is the git directory's `commondir` target, else the git directory itself.
- **No symlinks.** `.git`, the `gitdir:` target and the `commondir` target are `lstat`-ed and refused when they
  are links. Every pointer file is read `O_NOFOLLOW`, regular files only, capped at `MAX_POINTER_BYTES`.
- **Consistency.** A `gitdir:` target must hold `HEAD`. When it holds a `gitdir` back-pointer (a linked
  worktree), that back-pointer must resolve to the `.git` file that named it, so a `.git` file cannot borrow
  another checkout's git directory. A moved worktree fails this until `git worktree repair` is run.

## Public constants

- `MAX_POINTER_BYTES` (4096): the largest pointer file read.

## Public classes

### `GitDirError`

A `RuntimeError`: a `.git` pointer failed a custody or consistency check (including an oversized or non-regular
pointer file, so callers never see a bare `ValueError`).

## Public functions

### `git_dirs(start)`

**Args:**

- `start` (`Path`) — A path inside the checkout; resolved first, need not exist.

**Returns:** `tuple[Path, Path] | None` — `(git_dir, common_dir)`, both absolute with `..` resolved (the same path
for a main checkout), or `None` outside a git checkout.

**Raises:** `GitDirError` (see *Rules*); `OSError` when a pointer file exists but can't be read.

### `read_pointer(path)`

Read a small pointer file (`.git`, `gitdir`, `commondir`, `HEAD`, a loose ref) `O_NOFOLLOW`, stripped. Raises
`GitDirError` when it is not a regular file or is oversized, `OSError` when it can't be opened.

Exercised by `tests/test_update_lock.py`.
