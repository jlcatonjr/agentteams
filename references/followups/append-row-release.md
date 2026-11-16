# Follow-up: release the append_row LF-tail fix, then clear empty records from the governance logs

**Status:** open, waiting on the next release (written 2026-11-15) · **Source:** leftovers-1115 L4a/L4b (no remediation row) · **Suggested owner:** @orchestrator with @git-operations (release check, branch sweep), @security (clearance), operator (fast-forward, removal approval)

## Report

**What is wrong.** `agentteams.governance_logs.append_row` used to decide whether to write a line end before the new row by testing for the log's own terminator. In a CRLF log whose last row another writer had ended with a bare LF, it added CRLF anyway. Each such append left one empty record.

**Fix.** Commit `4296c64e` on `fix/append-row-lf-tail` changes the test to `("" if not raw or raw.endswith(b"\n") else newline)` and adds `test_append_row_adds_no_blank_line_after_a_bare_lf_row_in_a_crlf_log` in `tests/test_governance_logs.py`. It merged into `dev` as `f9b8b674` (2026-11-15). Verified 2026-11-15: `4296c64e` is an ancestor of `origin/dev`, not of `origin/main`. Local and remote branch tips are both `4296c64e`.

**Why it can recur.** The shared checkout stays on `main`, and other repos import it as an editable install. On 2026-11-15 an import from the shared checkout still ran the old line. Until the release PR (`dev` → `main`, `references/git-procedures.md` §G) merges and the checkout is fast-forwarded, every `governance_logs append` can add empty records.

**Evidence, 2026-11-15.**
- `references/security-decisions.log.csv` (gitignored; 9 columns `date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner`; mixed CRLF/LF): empty records 831, 840 and 843 were removed by a one-off helper. About 876 records on 2026-11-15, none empty (rows are still being appended).
- `.github/agents/references/conflict-log.csv` (gitignored; CRLF with 3 bare-LF rows): **one empty record now, record 426**, right after the three bare-LF rows (records 423-425, dated 2026-11-15). The bug fits; that append_row wrote it is (unverified).
- Clean on 2026-11-15: `references/agentteams-remediation-log.csv`, `references/redteam-findings.log.csv`, `references/mcp-needs.csv`, `.claude/agents/references/security-decisions.log.csv`, `.github/agents/references/security-decisions.log.csv`.

**What the tools already do (read on `origin/dev`).**
- `check_log` does **not** flag empty records. It skips them (`if r and len(r) != width`). So `governance_logs check` and `--verify-integrity` report these logs as well-formed.
- The security gate's reader (`cli/decision_log.py`, `_read_decision_rows_locked`) also skips empty records (`if r`). So they do not hide or forge a decision. The risk is log hygiene and record-number drift, not a gate bypass.
- `governance_log_repair` cannot remove them. `_plan_from_bytes` skips empty records (`if not cells or len(cells) == width: continue`), and it only re-joins comma-split cells. **The one-off helper (or a built-in successor) is still needed.**

**Who writes through append_row (grep of `origin/dev`, excluding tests).**
- `governance_logs` CLI `append` (`python -m agentteams.governance_logs append LOG --row-json …`). The orchestrator, security and conflict-auditor templates route every returned row through it, so any log in `LOG_NAMES` can be the target: `security-decisions.log.csv`, `agentteams-remediation-log.csv`, `redteam-findings.log.csv`, `mcp-needs.csv`, `conflict-log.csv`.
- `interop_grant_check.record_opt_ins` writes `references/security-decisions.log.csv` in a **destination team dir**, which can be another repository.
- `log_paths` looks for those names under `references/`, `.github/agents/references/` and `.claude/agents/references/`.

**Known vs unknown.** Known: the fix, its tests, the four empty records above. Unknown: whether consumer repos that use this editable install have empty records (not scanned), and whether an open release PR exists (`gh pr list` returned nothing here; unverified).

**Workaround until the release** (verified 2026-11-15). An agent can append without the bug by importing `append_row` from an extracted `origin/dev` copy instead of the shared checkout:
```bash
mkdir -p "$TMPDIR/devpkg" && git archive origin/dev agentteams | tar -x -C "$TMPDIR/devpkg"
python3 -I -c "
import os, sys; sys.path.insert(0, os.path.join(os.environ['TMPDIR'], 'devpkg'))
import agentteams.governance_logs as g
assert g.__file__.startswith(os.path.join(os.environ['TMPDIR'], 'devpkg')), g.__file__
# g.append_row(pathlib.Path(LOG), ROW)
"
```
The `assert` is required: if the import falls through to the editable install, the old line runs. On 2026-11-15 the security log ended with a bare-LF row written by another writer, and an append made this way added no empty record. Remove `$TMPDIR/devpkg` once the release is in the shared checkout.

**The one-off helper**, `tmp/by-week/2026-W46/leftovers-stripblank.py` (local, untracked; may be absent). Contract:
- Run from repo root: `python3 -I tmp/by-week/2026-W46/leftovers-stripblank.py preview`, then `… apply N`.
- Hard-coded to `references/security-decisions.log.csv`. It needs changing before use on another log.
- Removes only physical lines that are exactly `\r\n` or `\n` at a record start, outside quotes. All other bytes are kept.
- `apply N` refuses unless it finds exactly N empty records.
- Takes `flock` on the log, as `append_row` does.
- Proves before writing that the non-empty records are identical and in order, only line ends are removed, and no empty record is left. Re-runs `check_log` after writing.
- Writes the backup first, exclusive create plus fsync, to `tmp/quarantine/2026-11-15-security-log-blanklines/security-decisions.log.csv.before`. Because of the exclusive create, a second run with the same folder fails before it writes anything. Set a new folder.
- Rewrites in place through the locked descriptor. If the write or verify fails, it puts the original bytes back. It writes `manifest.json` (removed records, sha256 before and after).

**Backup.** `tmp/quarantine/2026-11-15-security-log-blanklines/` holds `security-decisions.log.csv.before` (512261 bytes) and `manifest.json`. Keep it. **Never restore it with plain `cp`.** Rows were appended after it was taken, and `cp` would drop them (the manifest's own restore line says this too).

## Plan

**Preconditions and constraints.**
- The shared checkout stays on `main`. Update it only with `git switch main` and `git pull --ff-only`. Never `git reset --hard`, `git checkout -- .` or `git clean -fd` there.
- Governance logs are append-only. The only exception is a cleared repair.
- A destructive edit to a governance log needs a recorded `@security` PASS first (C-5), plus the operator's approval of the exact preview.
- The sandbox cannot commit or push, and its `.git/worktrees` is read-only. Code changes go through the operator or a session that can push.
- A cloud session will not have the gitignored logs, the helper or the quarantine folder. Steps 2, 3 and 5 run on this machine.

**Steps.**
1. **Confirm the release** (@git-operations reads; operator fast-forwards the checkout). After the release PR merges: `git fetch origin`, then `git merge-base --is-ancestor 4296c64e origin/main`. The operator fast-forwards the shared checkout (`git switch main && git pull --ff-only`). Done when the ancestry check exits 0, `HEAD` equals `origin/main`, and the editable-import check (Verification) prints `new`.
2. **Scan every governance log** (@orchestrator, read-only). Run the empty-record scan (Verification) in this repo. Also run it in any consumer repo the operator names. Done when every log is listed with its empty-record numbers.
3. **Remove empty records** (@security clears; operator approves; @orchestrator runs). Only if step 2 found any. Expected now: `conflict-log.csv` record 426.
   - Prefer a built-in: open an item to teach `governance_log_repair` to drop empty records. Proposal: report them from `check_logs`/`--verify-integrity` as their own finding. Do **not** report them from `check_log` itself: `append_row` and the security gate refuse on any `check_log` problem, so one blank line would block every append and every gate decision.
   - Until a built-in exists, use the helper with its log path and quarantine folder set per log: preview, `@security` PASS naming the log, record numbers and preview sha, operator approval, then `apply N`.
   - Done when a re-scan shows 0 empty records and `governance_logs check .` passes.
4. **Branch sweep** (@git-operations dry run; `@security` PASS, authenticated by the operator's one-use waiver, to apply). At the next §E sweep, `fix/append-row-lf-tail` should read Merged rather than Integrated. Use `agentteams --branch-inventory --branch-report tmp/by-week/<week>/branch-audit`, then `--branch-cleanup <plan.json>` as a dry run. Per `references/git-procedures.md` §E: "@security records PASS for action branch-cleanup:<plan sha256> (printed above). This repo enforces signed decisions, so the operator authenticates it with a one-use waiver instead". Then `--branch-cleanup <plan.json> --apply`. Done when the branch is gone locally and on origin, and a row is in `references/branch-deletions.log.csv`.
5. **Decide retention of the backup** (operator decides). Options: keep it until a later sweep, or delete it once step 3 is verified and a later check is clean. Deleting needs @cleanup plus a `@security` PASS. Done when the decision is recorded in the daily work summary.

| step | agent | action | inputs | outputs | status | notes | depends_on |
|---|---|---|---|---|---|---|---|
| 1 | @git-operations + operator | confirm 4296c64e on origin/main; ff shared checkout; check import | release PR merged | ancestry exit 0; import prints new | pending | operator-only: the ff and the release merge approval | |
| 2 | @orchestrator | scan all governance logs for empty records | logs under LOG_DIRS; consumer repos | list of empty records per log | pending | read-only; local machine only | 1 |
| 3 | @security + operator + @orchestrator | remove empty records with a cleared tool | step 2 list; helper or built-in | logs with 0 empty records; backups | pending | operator approves the preview; PASS first | 2 |
| 4 | @git-operations | branch sweep for fix/append-row-lf-tail | branch-inventory report | branch deleted; deletions log row | pending | @security PASS for branch-cleanup:<plan sha256>, authenticated by operator one-use waiver (§E) | 1 |
| 5 | operator + @cleanup | decide quarantine backup retention | tmp/quarantine/2026-11-15-security-log-blanklines/ | recorded decision | pending | never restore with plain cp | 3 |

**Verification** (from the repo root).
```bash
git fetch origin && git merge-base --is-ancestor 4296c64e origin/main && echo released
git rev-parse HEAD origin/main    # from the shared checkout root; must match
python3 -c "import inspect, agentteams.governance_logs as g; print(g.__file__); print('new' if 'raw.endswith(b\"\\\\n\")' in inspect.getsource(g.append_row) else 'old')"
python3 -c "
import csv, io, pathlib
from agentteams.governance_logs import log_paths
for p in log_paths(pathlib.Path('.')):
    rows = list(csv.reader(io.StringIO(p.read_bytes().decode('utf-8'), newline='')))
    print(p, [i for i, r in enumerate(rows) if not r])"
python -m agentteams.governance_logs check .
python -m pytest tests/test_governance_logs.py -q
```

**Rollback.**
- Step 1 and step 4 change no log. A branch deletion is recoverable from its tip `4296c64e`, which is in `dev` history.
- Step 3: the backup is the original bytes as of the run. If rows were appended since, never copy it back. Diff it against the live log and re-insert only the lost bytes by hand, under the log's flock, with `@security` clearance.

**Files touched:** step 3 writes the affected logs in place (all gitignored) plus a new `tmp/quarantine/<date>-<log>-blanklines/` per log. Step 4 deletes the branch and appends to `references/branch-deletions.log.csv`. A built-in change would touch `agentteams/governance_logs.py`, `agentteams/governance_log_repair.py` and their tests, on a branch from `origin/dev`. **Not touched:** tracked files in the shared checkout, `tmp/quarantine/2026-11-15-security-log-blanklines/`.

**Open questions.**
- Which consumer repos append through this editable install? Do any of their logs have empty records? (unverified)
- Should the bare-LF writers be found and fixed too? The fix stops the empty records, but the line endings stay mixed. Which writer ends rows with bare LF is (unverified).
- Should a remediation row be filed for "check_log/repair blind to empty records"?
