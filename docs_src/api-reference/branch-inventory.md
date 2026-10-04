# `branch_inventory` — AgentTeamsModule

Read-only branch inventory and deletion plan. It is the classifier behind
[`--branch-inventory`](../cli-reference.md#branch-lifecycle), and the executable form of the
emitted `references/branch-lifecycle.reference.md`.

> *Source: `agentteams/branch_inventory.py`* (integrity-pinned)

**Why it exists.** baseAgent's 2026-10-04 audit found 66 of 67 branch refs already merged, with no
layer of the generated team ever deleting one (`references/branch-lifecycle-policy.handoff.md`).
The handoff's ancestry-only rule fits local `--no-ff` merges. On a squash-merging repository
(agentteams itself) it misclassifies nearly every merged branch: a squash rewrites the commits,
and `git cherry` only recognizes a squash of a single-commit branch. This module therefore adds
the **Merged-by-PR** state, verified through the GitHub API.

## Public API

| Name | Purpose |
|---|---|
| `InventoryConfig` | Inputs: `repo`, `push_remote`, `default_branch`, `stale_days`, `release_pattern`, `maintained_patterns`, `operator_emails`, `use_api`, `fetch`, `cite_exclude`, `now` |
| `build_inventory(cfg, *, api=None, api_factory=None) -> dict` | `{"meta", "refs", "plan"}`. Runs only `git fetch --prune` and read commands; never writes a ref |
| `deletion_plan(meta, refs) -> dict` | Plan items, each with `run` and `restore` commands, plus `sha256` over its binding fields |
| `plan_digest(plan) -> str` | Recomputes the sha256 used to detect an edited plan and to bind a clearance |
| `item_commands(item, remote) -> dict` | The exact leased commands, and the restore commands, for one item |
| `write_reports(inventory, out_dir) -> list[Path]` | Writes `branch-inventory.csv`, `branch-inventory.json` and `branch-deletion-plan.json` |
| `summary_line(inventory) -> str` | The Output Contract value (`clean` or `N merged-undeleted \| …`) |
| `GitHubAPI(repo)` | Read-only REST client. It uses `GH_TOKEN`/`GITHUB_TOKEN` over urllib, otherwise the `gh` CLI. `get` returns `ok`, `notfound` or `unknown` |
| `github_repo_of(url)` | `owner/name` for a github.com remote, else `None` |
| `STATES`, `CSV_COLUMNS` | The ten states in evaluation order, and the CSV column order |

## State model

States are evaluated in order, and the first match wins:
1. **default**
2. **protected** (API)
3. **evergreen**: a workflow `branch:`/`branch=` name. A templated name becomes a prefix pattern;
   a name that is variable from its first character is ignored.
4. **release-maintained**
5. **release-finished**
6. **merged**: an ancestor that owns at least one commit off the default branch's first-parent
   history. The owned commits are found by binary search for the merge point. An ancestor that
   owns none and is idle beyond *N* days is also merged (a fast-forward or abandoned empty
   branch).
7. **merged-by-pr**: the PR's head repository is the push remote's, its base is the default
   branch, it is merged, `head.sha` equals the tip, and its merge commit is reachable from the
   default branch.
8. **patch-equivalent**
9. **active**: last activity within *N* days. Activity is the tip's commit date or, for local
   refs, the reflog entry time.
10. **stale-unmerged**

**Holds** keep a branch whatever its state (`holds` column):
- `owner:<emails>`
- `link`
- `open-pr:head`, `open-pr:base`
- `worktree`, `worktree:prunable`
- `pinned`
- `pr-lookup-unknown`
- `api-unknown`: blocks every remote deletion when open PRs or protection could not be checked
- `pr-ref-missing`

**Actions:** `keep`, `delete`, `tag-then-delete`, or `ask-operator` (patch-equivalent and
stale-unmerged).
