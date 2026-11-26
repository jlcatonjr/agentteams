# `script_census` — AgentTeamsModule

Lists the scripts under one or more directories and marks which of them are copies.

> *Source: `agentteams/script_census.py`*

---

**Why it exists.** Sessions leave scripts behind: a query written for one question, a driver copied
and adjusted each week. A review of them starts with two facts that are tedious to gather by hand:
which scripts exist, and which are only copies of something else. These two commands gather them.
They are the first steps of the review in `references/script-consolidation.reference.md`.

**What it does.**

- `inventory` walks each root and writes one CSV row per script, with one meaning per column.
- Everything it leaves out is counted on stderr by reason, so the inventory shows its own gaps.
- `mark-copies` reads that CSV and adds a `copy_kind` column.
- Both are read-only and write only to stdout and stderr.

**What it never does.** It never follows or reads a symbolic link below a root, and it refuses a
root that is itself a link. It never enters a nested checkout or project. It never runs git inside
a scanned folder: git is asked only about the one repository you name (where its top is, and what
it tracks), and the blob ids of scanned files are computed in Python.

**Mind the `root` column.** A root is written to the CSV exactly as you gave it. Give relative
roots when the output will be saved in the repository, so that no home path lands in a file.

**Not in this module.** Classifying scripts into families, ranking them by use, and running across
several repositories.

## Command line

```
python -m agentteams.script_census inventory ROOT [ROOT ...] [--ext LIST] [--max-bytes N]
python -m agentteams.script_census mark-copies [--repo DIR] [--max-bytes N] [--snapshot-marker NAME] < inventory.csv
```

The two are meant to be piped:

```
python -m agentteams.script_census inventory tmp scripts | python -m agentteams.script_census mark-copies
```

Exit status: 0 when the table was written; 2 when a root is not a directory or is a symbolic link,
the input is not an inventory, a row's `path` is absolute or has a `..` component, `--max-bytes` is
negative, `--ext` names no extension, or `--repo` is not a git repository.

## Inventory columns

| Column | Meaning |
|---|---|
| `path` | The file, relative to its root, with `/` separators |
| `size` | Bytes |
| `lines` | Lines, counting a last line that has no line ending |
| `sha256` | SHA-256 of the bytes |
| `ext` | The extension in lower case, or empty for a file that starts with `#!` |
| `root` | The root exactly as it was given |

Rows are sorted by root, in the order given, and then by path. Running it twice on an unchanged
tree gives the same output.

## What the inventory leaves out

Each is counted on stderr under the name shown.

| Reason | What is left out |
|---|---|
| `pruned-dir` | A folder named in `PRUNE_DIR_NAMES`: caches, virtual environments, build output, `.git`, backups. `tmp` is walked, since one-off scripts live there |
| `nested-checkout` | A folder below the root that holds a `.git` entry or a `pyproject.toml`. Its scripts belong to its own inventory |
| `symlink` | A symbolic link to a file or a folder |
| `oversize` | A file larger than `--max-bytes` (default 1,000,000) |
| `other-extension` | A file whose extension is not a script extension, or a file with no extension that does not start with `#!` |
| `unreadable` | A file that could not be read as a regular file |
| `unreadable-dir` | A folder that could not be listed |

## Copy kinds

`mark-copies` checks in this order and writes the first that applies.

| `copy_kind` | Meaning |
|---|---|
| `snapshot` | A folder in `path`, or in a relative `root`, matches a snapshot marker (folders above an absolute root are not looked at): the backup folder, `snapshots`, `snapshots-*`, `.worktrees`, `worktrees`, `archive`, or a pattern given with `--snapshot-marker` |
| `missing` | The file can no longer be read below its root: it is gone, larger than `--max-bytes`, or now reached through a symbolic link |
| `tracked-duplicate` | The file is not itself tracked by the repository, and its bytes equal a file the repository tracks |
| `authored` | None of the above |

Two tracked files with the same bytes are both `authored`: the repository already records that.

## Key surface

### inventory(roots, *, extensions, max_bytes)

Returns `(rows, pruned)`: the rows as dicts keyed by `INVENTORY_COLUMNS`, and a counter of what was
left out by reason. Raises `CensusError` when a root is not a directory or is itself a symbolic link.

### mark_copies(rows, *, repo, snapshot_markers, max_bytes)

Returns the rows, in order, each with `copy_kind` set. A relative `root` is read from the current
directory, as `inventory` wrote it. The rows are text, so they are not trusted: `path` must be
relative with no `..` component. Raises `CensusError` when a row has no `path` or `root`, a `path`
is absolute or has a `..` component, or `repo` is not a git repository.

### tracked_blobs(repo)

The blob ids of the content `repo` tracks, each with the repository paths that hold it. One
`git ls-files` at the top of `repo`, with inherited `GIT_*` variables removed so that a `GIT_DIR` in the
environment cannot redirect it. Submodule entries are left out.

### main(argv)

The command line. Returns the exit status.

### Constants

`PRUNE_DIR_NAMES`, `SCRIPT_EXTENSIONS`, `MAX_BYTES`, `SNAPSHOT_MARKERS`, `INVENTORY_COLUMNS`,
`COPY_KINDS`, `GIT_TIMEOUT`. `PRUNE_DIR_NAMES` extends the set `ingest` keeps; it does not restate it.

### CensusError

A request that cannot be carried out. A subclass of `ValueError`.
