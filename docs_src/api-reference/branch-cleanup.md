# `branch_cleanup` — AgentTeamsModule

Guarded branch deletion, behind [`--branch-cleanup`](../cli-reference.md#branch-lifecycle)
(a plan, under a `@security` clearance) and `--branch-post-merge` (one just-merged branch, under
an operator Ed25519 `branch-delete` grant).

> *Source: `agentteams/branch_cleanup.py`* (integrity-pinned)

## Authorization

| Mode | Covers | Authorization |
|---|---|---|
| `run_cleanup` | a whole plan from [`branch_inventory`](branch-inventory.md) | `security_gate` PASS for the action `branch-cleanup:<plan sha256>`, consumed on use (C-5) |
| `run_post_merge` | only a branch whose tips are the second parent of the push remote's default-branch tip, ancestry-verified | A valid `branch-delete` grant (`agentteams.cli.grants`: Ed25519 only, unexpired, approver on the team roster), with fewer runs recorded against it than `max_uses` |

Both modes refuse while a `@security` HALT on `branch-delete` (`HALT_FAMILY`) is unretracted
(C-2).

## Guards

1. **Plan integrity:** a plan whose sha256 no longer matches its contents is refused
   (`load_plan`).
2. **Drift:** the executor re-inventories first and skips any item whose tip or verdict drifted.
3. **Per-ref re-check:** ancestry, or a PR re-query plus `refs/pull/<n>/head` on the remote.
4. **Order and commands:** local refs first (`branch -d`, or `update-ref -d <ref> <sha>` for a
   PR-merged branch), then remote refs with `push --force-with-lease=refs/heads/<b>:<sha> --delete`.
5. **Tags:** annotated `archive/<branch>` tags are created only if absent, never moved, and
   confirmed with `ls-remote`.
6. **Failure:** the first failure stops the run.

## Ledger

`references/branch-deletions.log.csv` (`LEDGER_COLUMNS`) is hash-chained through `prev_digest`.
`read_ledger` raises on any broken link. Each deletion is recorded as rows in this order:
1. `skipped` (a failed re-check; the run stops) or, for a release branch, `tagged`, which comes
   before `attempt`;
2. `attempt`, written **before** the delete command runs;
3. `deleted` or `failed`, with the old SHA and the restore command.

`grant_uses` counts the distinct runs that attempted a deletion under a grant.

## Public API

`run_cleanup`, `run_post_merge`, `execute_items`, `load_plan`, `clearance_action`,
`find_branch_grant`, `grant_uses`, `read_ledger`, `append_ledger`, `CleanupError`, and the exit
codes `EXIT_OK` (0), `EXIT_FAIL` (1) and `EXIT_UNAUTHORIZED` (3).
