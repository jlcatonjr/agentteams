# `ref_branch` — AgentTeamsModule

Names a repository's default-branch ref, reads files from it without touching the working tree, and
reports how stale that view may be.

> *Source: `agentteams/ref_branch.py`*

---

**Why it exists.** A verdict such as "no tool covers this" is only as good as the tree it was read
from. A local checkout can lag the default branch, so a tool that exists upstream looks missing. The
remote-tracking ref lags too, by however long ago the last fetch was. An audit that judges coverage
should read from the default branch and print the ref and its age beside the verdict.

**What it does.**

- It resolves the reference ref in this order: `origin/HEAD`; `origin/main` or `origin/master`; a
  local `main` or `master`. If none exists it raises. It never guesses.
- It reads file content with `git cat-file blob`, so no filter driver or textconv runs and the
  working tree is not consulted.
- It reports how many commits the checkout is behind the ref and the hours since the last fetch.
- It never fetches and never writes.

**How git is run.** In the given repository only, through `git_exec.run_git(hardened=True)`, with
inherited `GIT_*` variables removed so that a `GIT_DIR` in the environment cannot redirect a query,
and with a 20-second limit per command.

## Key surface

### default_ref(repo)

The ref that stands for the default branch, such as `origin/main`. Raises `RefBranchError` when
`repo` is not a git repository or has none of the refs above.

### show(repo, path, *, ref)

The text of `path` at the ref, or `None` when it is not a file there. `path` must be a plain
repository-relative path: an absolute path, a `..` component, a leading `-` or a `:` is refused.

### changed_since(repo, path, days, *, ref)

Commits on the ref that touched `path` in the last `days` days, newest first, as
`<short sha> <date> <subject>`. Use it before reporting a tool as missing: it may have been added
this week.

### freshness(repo, *, ref)

Returns a `Freshness`:

| Field | Meaning |
|---|---|
| `ref` | The reference ref |
| `remote` | Whether it is a remote-tracking ref. `False` means only a local branch was found |
| `branch` | The checked-out branch, or `(detached)` |
| `behind` | Commits on the ref that `HEAD` lacks, or `None` |
| `fetch_age_hours` | Hours since the last fetch, or `None` when the repository was never fetched |

`Freshness.describe()` gives the line to print beside a verdict, for example
`origin/main (fetched 3.2 h ago; checkout 4 commit(s) behind; on feature-x)`.

### main(argv)

The command line.

## CLI

```
python -m agentteams.ref_branch [REPO] [--json] [--max-fetch-age HOURS]
python -m agentteams.ref_branch [REPO] --show PATH
```

- With no option it prints the `describe()` line for `REPO` (default: the current directory).
- `--max-fetch-age HOURS` exits 1 when the last fetch is older than that, or there never was one.
- `--show PATH` prints the file as it is on the ref, with the ref line on stderr. It exits 1 when the
  path is not a file there.
- Exit status 2 means the default ref could not be resolved.

## Limits

- The answer is as current as the last fetch. Run `git fetch` first when it matters.
- A repository whose default branch is not named by `origin/HEAD`, `main` or `master` is refused.
- Other resolvers remain in `branch_inventory`, `release_required_checks` and the vulnerability-scan
  modules. `api_impact` uses this one. Moving the others here is a separate, reviewed change, because
  some of those modules are integrity-pinned.
