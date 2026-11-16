# Follow-up: governance-log repair backups land untracked and unignored next to the log

**Status:** open (written 2026-11-15) · **Source:** remediation log line 739 / record 738 (that log is local and gitignored; its text is quoted below) · **Suggested owner:** @pipeline-core-expert (design), @primary-producer (code), @test-suite-expert (tests), @code-hygiene (audit), @security (review: it touches governance-log backup handling)

> "governance_log_repair writes its backup as LOG.bak-<UTC timestamp> next to the log. For references/security-decisions.log.csv, .gitignore ignores only the exact log name, so the two 2026-11-11 repair backups sat untracked in references/ for four days where a broad git add would commit them (quarantined 2026-11-15, leftovers-1115 L3). Fix: write repair backups under tmp/quarantine/ or add a references/*.log.csv.bak-* ignore rule." Touch points: `agentteams/governance_log_repair.py; .gitignore`.

## Report

**What is wrong.** `apply_repair` writes a full copy of the pre-repair log as `<log>.bak-<YYYYMMDDTHHMMSSZ>` in the log's own directory. Nothing ignores that name. Governance logs are private operational records (security decisions, red-team findings). A broad `git add` would publish the copy even where the log itself is ignored. The tool ships in the package, so generated teams in other projects have the same exposure, and agentteams writes no ignore rule for them.

**Evidence (origin/dev).**
- `agentteams/governance_log_repair.py` `_write_backup`: `path.with_name(f"{path.name}.bak-{stamp}")`, `O_CREAT|O_EXCL|O_NOFOLLOW`, mode 0600, then fsync of the parent dir.
- `apply_repair` returns the backup path. The CLI (`agentteams/governance_logs.py` `main`, `repair LOG --apply DIGEST`) prints `backup: <path> (remove it once you have checked the log)`. No CLI flag sets the backup location.
- No code reads the backup. The in-function "restore" on failure rewrites the in-memory `raw` bytes, not the file. The backup is for a manual restore only.
- Targets: `LOG_NAMES` = security-decisions.log.csv, agentteams-remediation-log.csv, redteam-findings.log.csv, mcp-needs.csv, conflict-log.csv; `LOG_DIRS` = references, .github/agents/references, .claude/agents/references. `apply_repair` accepts any path whose basename is in `LOG_NAMES`.
- `.gitignore` on dev ignores the four `references/` logs by exact name (lines 218-220, 237). `git check-ignore` in this checkout: `references/<each log>.bak-x` is NOT ignored; `.github/agents/references/conflict-log.csv.bak-x` and `.claude/agents/references/...bak-x` ARE ignored (by `.github/agents/*` and `.claude/`).
- Generated teams: `git grep gitignore origin/dev -- agentteams` finds no emitter of governance-log ignore rules. The only root `.gitignore` writer is `env_hygiene.py --fix` (`.env` lines only).
- Precedent for a fix: `agentteams/backup.py` `ensure_backup_root_ignored` writes a self-ignoring `.gitignore` (`*`) into `.agentteams-backups/` (remediation log line 662 / record 661). `wasm_runtime.py` does the same for its install dir.
- Docs: `docs_src/api-reference/governance-log-repair.md` line 36 names `LOG.bak-<UTC timestamp>` and says "Remove it once the log has been checked." Tests: `tests/test_governance_log_repair.py` asserts `backup.read_bytes() == before` and `not list(tmp_path.glob("*.bak-*"))` (the latter would pass vacuously if backups moved to a subdirectory).
- Local: the two backups are in `tmp/quarantine/2026-11-15-security-log-baks/` with `manifest.json` (sha256 before/after equal). Its `restore` field says to move them back to `references/`. That manifest is a local, untracked historical record of the quarantine; this plan does not rewrite it.
- What walks a team dir's `references/` (grep of `origin/dev` `agentteams/` for `rglob`/`iterdir`/`os.walk`), and so would see a new `<log dir>/.governance-log-backups/` under `.claude/agents/references/` or `.github/agents/references/`:
  - **CAI export picks it up.** `interop_helpers.capture_references` (called from `interop.py` export) walks `<team dir>/references/` with `rglob("*")` and adds every readable text file as a CAI `references[]` entry. The `.bak-*` copies (CSV text) and the `.gitignore` would be exported, and an exported CAI document or canonical tree lands wherever the operator writes it, outside the self-ignore. (The live logs in that dir are already captured the same way; that is a separate, existing exposure.) `canonical.load_canonical` reads `references/` back with the same `rglob("*")`.
  - **Pre-sync backup copies it.** `backup.py` (pre-sync-projection, around line 816) copies every file under the agents dir by `rglob("*")`, excluding only `.agentteams-backups`. The repair backups would be copied into `.agentteams-backups/<stamp>/references/.governance-log-backups/`, which is itself self-ignored, so this multiplies private copies but does not unignore them.
  - **Not affected:** the memory index (`cli/artifacts.py` takes `references/**/*.md` only); `interop.py` agent discovery (skips `references/` via `_NON_AGENT_DIRS`); agent-file globs (`*.md`, `*.agent.md`) in audit, sync_roster, fleet and adopt-orphans; the integrity manifest (enforcement modules, not team dirs). `stale_detector` uses `git ls-files` in a repo, so ignored files are skipped; outside a repo it falls back to `rglob("*")` (not traced further, unverified).
  - **Sandbox.** A Claude Code sandbox may deny writes under `.claude/`. Whether `.claude/agents/references/.governance-log-backups/` is writable from a sandboxed session is (unverified).

**Known vs not known.** Known: naming, location, no reader, no ignore rule here or in generated teams. Not known: whether any consumer project commits its logs (then a tracked backup might be wanted) (unverified); whether consumers ignore `tmp/` (unverified); whether the `stale_detector` non-git fallback would flag the new dir (unverified). Known now: CAI export and the pre-sync backup would pick it up (see Evidence).

**Risk of leaving it.** Each repair leaves a full private-log copy one `git add -A` from publication, in this repo and in every consumer.

## Options compared

| Option | Covers generated teams | Backup discoverable | Works without tmp/ | Restore docs |
|---|---|---|---|---|
| A. Ignore rule `references/*.bak-*` in this `.gitignore` | No | Yes, unchanged | Yes | Unchanged |
| B. Write under `<root>/tmp/quarantine/<date>-log-repair/` | Only if consumer ignores tmp/ (unverified); needs project-root discovery from a log path | Yes, path printed | No (must create tmp/, maybe unignored) | Changes |
| C. Both A and B | Partly | Yes | No | Changes |
| D. Under the git dir | Yes | Poor (hidden; `.git` is a file in worktrees; no git dir in non-repo roots) | Yes, if git | Changes |
| E. (recommended) Sibling dir `<log dir>/.governance-log-backups/` with a self-ignoring `.gitignore` (`*`), as in `backup.py`; plus rule A as belt-and-braces here | Yes, no consumer config needed | Yes, next to the log, path printed | Yes | Small change (one path) |

**Recommendation: E.** It needs no root discovery, works in any directory, and copies a reviewed precedent. Use a distinct name, not `.agentteams-backups`, so `backup.prune_backups` never touches it (whether prune would reach it is unverified; a distinct name avoids the question). Keep the existing `.bak-<stamp>` filename inside it so the quarantine manifest and docs still read naturally.

## Plan

**Preconditions and constraints.**
- Work in a worktree: `git worktree add --no-track ../agentteams-wt/repair-backup-dir -b fix/repair-backup-dir origin/dev`. Keep the shared checkout on `main`.
- Merge into `dev` with `--no-ff` behind the §G local gate (`references/git-procedures.md`). Push with `git push origin <branch>` (no `-u`).
- The Claude Code sandbox cannot add worktrees, commit or push: hand those commands to the operator.
- @code-hygiene audits the diff. @security reviews it (backup handling of private logs; symlink safety of the new dir).
- Rule 9: copy the table below into `tmp/by-week/<week>/repair-backup-dir.steps.csv` before step 1.

**Steps.**
1. @pipeline-core-expert: confirm design E and dir name `.governance-log-backups`, and decide the CAI-export question: exclude `.governance-log-backups` in `interop_helpers.capture_references` (and the `canonical.py` reader), as `backup.py` already excludes `.agentteams-backups`. Done when the brief names the dir, mode 0700, no-follow creation, the self-ignore file, and the export exclusion.
2. @primary-producer: in `_write_backup`, open the log's parent with `os.open(parent, O_RDONLY|O_DIRECTORY)`; `os.mkdir(".governance-log-backups", 0o700, dir_fd=parent_fd)` (ignore `FileExistsError`); then `os.open(".governance-log-backups", O_RDONLY|O_DIRECTORY|O_NOFOLLOW, dir_fd=parent_fd)`, which fails on a symlink or a non-directory. Create `.gitignore` (`*`) and the backup relative to that descriptor (`dir_fd=`) with `O_CREAT|O_EXCL|O_NOFOLLOW`, 0600; an existing `.gitignore` is kept, never overwritten; fsync the dir descriptor. No path is re-resolved between the check and the write, so there is no mkdir-then-check race. (POSIX only; on platforms without `dir_fd`/`O_NOFOLLOW`, state the remaining race in the docstring.) Exclude the dir in `capture_references`. Update the module docstring and the CLI message. Done when `apply_repair` returns a path under the new dir.
3. @primary-producer: add `references/*.bak-*` to this repo's `.gitignore` next to lines 218-220. Done when `git check-ignore -v references/security-decisions.log.csv.bak-x` matches.
4. @test-suite-expert: update tests. Backup lives in `tmp_path/.governance-log-backups/`; that dir holds `.gitignore` == `*\n`; a pre-existing `.gitignore` is kept; a symlinked backup dir is refused with nothing written; a CAI export of a team dir holding the backup dir omits it; change the no-backup assertion to `rglob("*.bak-*")`. Done when tests pass.
5. @primary-producer: update `docs_src/api-reference/governance-log-repair.md` line 36 and add a CHANGELOG entry. Done when docs name the new path.
6. @code-hygiene then @security: audit and review. Done when both record a pass.
7. @git-operations via operator: commit, gate, merge to dev `--no-ff`, push. Done when dev contains the merge.
8. @orchestrator: governance logs are append-only, so do not edit record 738. Append a new cross-reference row naming the fix and record 738 through `agentteams.governance_logs.append_row`. Until the append_row LF-tail fix (dev `f9b8b674`) is released, import it from an extracted `origin/dev` copy, not the shared checkout (see `append-row-release.md`, Workaround). Record 738's status stays open for a later verifier. The quarantine `manifest.json` (local) is not rewritten; if those backups are ever restored, the destination follows the new rule, `references/.governance-log-backups/`, not `references/`. Done when the new row is in the log and `governance_logs check .` passes.

| step | agent | action | inputs | outputs | status | notes | depends_on |
|---|---|---|---|---|---|---|---|
| 1 | pipeline-core-expert | Confirm design E and dir name | this doc; backup.py ensure_backup_root_ignored | brief | pending | distinct name from .agentteams-backups | |
| 2 | primary-producer | Move backup into self-ignoring sibling dir; exclude it from CAI export | governance_log_repair.py; governance_logs.py; interop_helpers.py | code change | pending | dir_fd + O_NOFOLLOW + O_DIRECTORY; 0700; never overwrite .gitignore | 1 |
| 3 | primary-producer | Add references/*.bak-* ignore rule | .gitignore | .gitignore | pending | belt-and-braces for this repo | 1 |
| 4 | test-suite-expert | Update and add tests | tests/test_governance_log_repair.py | tests | pending | rglob not glob | 2 |
| 5 | primary-producer | Update API doc and CHANGELOG | governance-log-repair.md; CHANGELOG.md | docs | pending | | 2 |
| 6 | code-hygiene | Audit diff, then security review | diff | audit and review notes | pending | @security for log-integrity | 3;4;5 |
| 7 | git-operations | Commit, gate, merge to dev --no-ff, push | branch | dev merge | pending | operator runs git | 6 |
| 8 | orchestrator | Append cross-reference row for remediation record 738 | remediation log; append_row from origin/dev copy | new log row | pending | append-only; Rule 13: do not flip status; manifest.json not rewritten | 7 |

**Verification.**
```
python -m pytest -q tests/test_governance_log_repair.py tests/test_root_doc_hygiene.py
git check-ignore -v references/security-decisions.log.csv.bak-x references/.governance-log-backups/x
python -m agentteams.governance_logs check .
git status --porcelain --ignored references/ | grep -i bak   # expect only !! (ignored) lines
```
Then run the full §G local gate.

**Rollback.** Revert the merge commit on dev (`git revert -m 1 <merge>`). Existing backups in a `.governance-log-backups/` dir stay ignored by their own `.gitignore`; leave them in place.

**Files likely touched.** `agentteams/governance_log_repair.py`, `agentteams/governance_logs.py` (CLI message only), `agentteams/interop_helpers.py` and possibly `agentteams/canonical.py` (export exclusion), `tests/test_governance_log_repair.py`, `.gitignore`, `docs_src/api-reference/governance-log-repair.md`, `CHANGELOG.md`.

**Must NOT be touched.** Any governance log's content; `tmp/quarantine/2026-11-15-security-log-baks/` (backup files and its `manifest.json`, a historical record); `agentteams/backup.py` (copy its pattern, do not import or change it unless @code-hygiene asks); `.vscode/tasks.json` in the shared checkout.

## Open questions for the operator
1. Accept dir name `.governance-log-backups`, or prefer another?
2. Should a later step prune old repair backups, or stay manual-delete as now?
3. Keep the two quarantined 2026-11-11 backups in `tmp/quarantine/`, or move them into the new dir once it exists?
