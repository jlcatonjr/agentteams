# Follow-ups ready for an agent

Each file in this folder is one open item: a report (what is wrong and the evidence) and a plan (steps, a steps
table, verification, rollback). An agent can pick one up from the file alone. The local logs these files cite
(`references/agentteams-remediation-log.csv`, `references/security-decisions.log.csv`) are gitignored, so each
file quotes the text it relies on.

**Citations.** "Remediation log line N / record N-1" gives the physical line number (the header is line 1) and the
csv record number (the header is record 0). Both name the same row.

**Before you start one:** copy its steps table into `tmp/by-week/<week>/<slug>.steps.csv` (Rule 9), re-check its
facts against the current `origin/dev` (these files were written on 2026-11-15 and `dev` moves), and set its
**Status** line here and in the file when you finish.

## Items

| File | Item | Status | Touches |
|---|---|---|---|
| [lineage-regen.md](lineage-regen.md) | `test_lineage_regenerates_byte_for_byte` fails on `dev` | open | `scripts/build_launcher_lineage.py`, `tests/test_launcher_lineage.py` |
| [timing-budget.md](timing-budget.md) | Two load-sensitive timing tests are deselected by hand in every gate run | open | `tests/conftest.py`, `pyproject.toml`, the two timing test files, a new workflow-check test, `references/git-procedures.md` §G |
| [repair-backup-location.md](repair-backup-location.md) | `governance_log_repair` backups land untracked beside the log | open | `agentteams/governance_log_repair.py`, `agentteams/governance_logs.py`, `agentteams/interop_helpers.py`, `agentteams/canonical.py`, tests, `.gitignore`, the repair docs |
| [handoff-trustroot-fp.md](handoff-trustroot-fp.md) | The handoff writer refuses trust-root words in commit messages | open | `agentteams/sandbox_handoff.py`, `tests/test_sandbox_handoff.py`, `CHANGELOG.md` |
| [dev-merge-template.md](dev-merge-template.md) | A built-in handoff for a local merge into `dev` that survives `dev` moving | open | `agentteams/sandbox_handoff.py`, its tests, `docs_src/api-reference/sandbox-handoff.md`, possibly the universal template, `references/git-procedures.md` §G |
| [append-row-release.md](append-row-release.md) | After the release: confirm the `append_row` fix on `main`, clear empty log records, sweep the branch | waiting on the next release | governance logs (local), `agentteams/governance_logs.py`, possibly `agentteams/governance_log_repair.py` |

## Order

Items that touch different files can run in parallel. Items that share a file run one after another.

- **Parallel, any time:** `lineage-regen`, `timing-budget`, `repair-backup-location`. They share only a possible
  `CHANGELOG.md` line; whichever merges later rebases that line.
- **One lane, in this order** (all edit `agentteams/sandbox_handoff.py`):
  1. remediation record 736 (local log), not written up here: make the handoff writer and `vulnscan/known.py` also
     refuse the additional instruction files that record names, by routing both through
     `governance_targets.target_is_elevated_constitution`. It only adds refusals, so it goes first;
  2. `handoff-trustroot-fp`;
  3. `dev-merge-template`. Its generated commit and merge messages need step 2's fix, and its gate needs
     `timing-budget` and `lineage-regen` done first, so that it carries no hand-written `--deselect`.
- **`timing-budget` and `dev-merge-template`** both edit `references/git-procedures.md` §G. `dev-merge-template`
  lands second, so it rebases its §G text onto `timing-budget`'s.
- **`append-row-release`** waits for the next release PR (`dev` → `main`). Its code part, a check that reports empty
  records, edits governance-log code next to `governance_log_repair.py`, so start it after `repair-backup-location`
  has merged.

## Shared rules

- Work in a worktree off `origin/dev` under `../agentteams-wt/`. The shared checkout stays on `main`.
- Merge into `dev` with `--no-ff` after the local gate (`references/git-procedures.md` §G).
- In the Claude Code sandbox an agent cannot add or remove worktrees, commit or push. Those steps go to the operator
  through `python3 -m agentteams.sandbox_handoff write` (§H).
- Destructive steps, governance-log edits and security-relevant code changes need `@security` clearance first.
  Trust roots (the integrity manifest, rosters, signing material, the constitution) need the operator.
- Code changes need a `@code-hygiene` audit before the merge. Close each item with `@conflict-auditor` (Rule 8).
- **The shared checkout runs `main`'s code.** A fix merged into `dev` (including the handoff writer's own fixes) is
  not what `python3 -m agentteams…` runs from the shared checkout until the release. To use it earlier, run from the
  item's worktree root and check `python3 -I -c 'import agentteams; print(agentteams.__file__)'`.
- **Governance logs are append-only.** A Rule 13 cross-reference is a new row, not an edit of the old one. Until the
  `append_row` fix (dev `f9b8b674`) is released, `main`'s `append_row` adds an empty record after a row that ends in
  a bare LF; import it from an extracted `origin/dev` copy instead (see `append-row-release.md`).
