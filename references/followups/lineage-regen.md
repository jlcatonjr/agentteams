# Follow-up: launcher lineage is stale on origin/dev after the HMAC key-file merge

**Status:** open (written 2026-11-15) · **Source:** remediation log line 732 / record 731 (that log is local and gitignored; its text is quoted below) · **Suggested owner:** @pipeline-core-expert (builder change), @test-suite-expert (test), @security (security-column and exclusion review), @git-operations + operator (worktree, commit, push)

> 2026-11-14, this-repo, ci: "origin/dev fails tests/test_launcher_lineage.py::test_lineage_regenerates_byte_for_byte: commit c5ae8582 (2026-11-14, 'fix(signing): HMAC key files need a recorded fingerprint') is on origin/dev but agentteams/data/launcher_lineage.json was not regenerated (last regenerated in e0672a6a). Run scripts/build_launcher_lineage.py, review the security column, and re-pin the integrity manifest on dev. Found by the followups-close Plan A section-G gate, which deselected it (operator picker) since the merged change is one doc row."

Related: line 698 / record 697 (confine-run.sh has a hidden pin chain; see Plan).

## Report

**What is wrong.** `scripts/build_launcher_lineage.py` walks `git log --follow HEAD -- agentteams/templates/universal/sandbox/confine-run.sh` across all parents. Two side-branch commits now in dev's history changed the template: c5ae8582 (to sha256 `706e0f7b…`) and ee45c106 (reverted it to `e2291065…`, main's version). Both landed through merge 99093448 (2026-11-14). The item-17 regenerations dc7b9abb and 2252fff0 were authored on a branch without them and landed later, through merge 42a683c3 (PR #500, 2026-11-15). That merge set the template to `026b5417…`. The committed lineage does not list c5ae8582 or ee45c106, so a rebuild differs. **The log's suggested fix does not work.** A plain rebuild orders entries by commit date. It puts ee45c106 (`e2291065…`) last, but the template on disk is `026b5417…`. So `test_the_newest_version_is_the_shipped_template` would start failing in its place. The rebuild also flags ee45c106 `security: true` (its subject is `fix(sandbox):`), although that commit removed a deny line from the launcher.

**Evidence (verified 2026-11-15 against origin/dev 33824c72).**
- The test (`tests/test_launcher_lineage.py:46`) compares `builder.serialise(builder.build())` with `agentteams/data/launcher_lineage.json`. It skips in a shallow clone unless `AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1`. The builder needs full history and exits 2 on a shallow clone.
- Template sha256: origin/dev `026b5417…`; origin/main `e2291065…`; c5ae8582 `706e0f7b…`; ee45c106 and 99093448 `e2291065…`.
- These dev pins all already equal `026b5417…`: the manifest entry for the template in `references/enforcement-integrity.json`, the last lineage entry (6e9193f3), `EXPECTED_LAUNCHER_SHA256` in `tests/test_mac_sandbox_augment.py`, and `_LAUNCHERS_AT_BASE` in `tests/test_integrity_wasm_pins.py`. The manifest pins the lineage JSON at `e268e553…`, which equals the committed file.
- A simulated build for origin/dev (the dev copy of the builder with `HEAD` replaced by `origin/dev`, run from a scratch dir) **differs**. Its last four entries are c69bb9aa, 6e9193f3, c5ae8582 (sec false), ee45c106 (sec **true**).
- The same build with c5ae8582 and ee45c106 dropped is **byte-identical** to the committed JSON.
- Since c5ae8582, no other commit on origin/dev touches the template, the lineage JSON, the builder, `agentteams/launcher_lineage.py` or the test (only the PR #500 commits c69bb9aa, 6e9193f3, dc7b9abb, 2252fff0, which predate it).
- CI does not run this test with history (checked on origin/dev 7563b693). In `.github/workflows/ci.yml` the `test` job uses `actions/checkout@v7` with no `fetch-depth`, so its clone is shallow (depth 1). Its "Run tests" step sets only `AGENTTEAMS_REQUIRE_BWRAP`, and no job sets `AGENTTEAMS_REQUIRE_LINEAGE_HISTORY`. Only `docs-build` and `template-lineage` use `fetch-depth: 0`, and `template-lineage` runs `tests/test_template_lineage.py` only. So the launcher-lineage test most likely skips on CI, and a green CI run proves nothing here.
- The builder script is not in the integrity manifest. Only `agentteams/data/launcher_lineage.json` and `agentteams/launcher_lineage.py` are pinned.
- Trust root: `agentteams/cli/governance_targets.py` lists `"enforcement-integrity"` in `GOVERNANCE_TARGET_STEMS`. Under CLAUDE.md Rule 15 the manifest is a trust root. The past re-pins (dc7b9abb, 2252fff0, c5ae8582) were still made by agent-co-authored commits via `agentteams --write-integrity-manifest`, after @security review (references/git-procedures.md §A.1 step 6).

**Known vs not known.** Known: the cause, and that the committed JSON is right once the two branch-only commits are excluded. Not known: whether any consumer rendered a launcher at `706e0f7b…` (it existed only on the feature branch for about 30 minutes; unverified). After the fix, such a copy would read `modified`, not `older`. Also not known: whether a generic rule (for example a first-parent-aware walk) can replace a named exclusion list. `--topo-order` is not that rule: it puts the right entry last but breaks the oldest-first date assertion (checked).

**Risk of leaving it.** The §G gate keeps deselecting the test, so a real launcher-lineage regression would pass unseen. CI does not catch it either: it most likely skips the test (shallow clone, see Evidence). So the local gate is the only place it runs. The next agent that follows the log text would rebuild, ship a wrong "newest" entry, and change a trust-root pin for nothing.

## Plan

**Preconditions and constraints.**
- Work in a worktree off origin/dev: `git worktree add --no-track ../agentteams-wt/lineage-branch-only -b fix/lineage-branch-only origin/dev`. Use a full clone: the builder refuses a shallow one. In a cloud session, `git fetch --unshallow` first.
- Merge into dev with `--no-ff`, behind the §G local gate (references/git-procedures.md §G/§H).
- In the Claude Code sandbox an agent cannot add or remove worktrees, commit or push. Those steps go to the operator through `python3 -m agentteams.sandbox_handoff write`.
- The preferred fix leaves `references/enforcement-integrity.json` and the lineage JSON unchanged. If a step does change either one, stop: it needs @security clearance and operator sign-off first (trust root, Rule 15 / C-5).
- Rule 12: the new exclusion is itself an audit target. The review must ask what else it could hide. The guard is step 3: an entry can only name a side-branch commit (never one on the first-parent mainline), never a security-reviewed commit, and only a pair whose net effect on the template is zero.

**Steps.**
1. Operator: create the worktree and branch above. Done when `git -C ../agentteams-wt/lineage-branch-only rev-parse --is-shallow-repository` prints `false`.
2. @pipeline-core-expert: in `scripts/build_launcher_lineage.py`, add `BRANCH_ONLY: dict[str, str]`, keyed by full 40-char sha with a reason, as `SECURITY_REVIEWED` is. List c5ae8582… ("branch-only launcher edit, reverted by ee45c106 before merge 99093448") and ee45c106… ("revert of c5ae8582; mainline went e2291065 → 026b5417 at 42a683c3"). Skip these commits in `build()`. Document the rule in the module docstring. Done when `python3 scripts/build_launcher_lineage.py --check` exits 0.
3. @test-suite-expert: extend `tests/test_launcher_lineage.py` so the exclusion list cannot hide a mainline commit (Rule 12). Assert, for every `BRANCH_ONLY` key:
   - it is 40 hex chars and an ancestor of HEAD (`git merge-base --is-ancestor <key> HEAD`);
   - it is **not** in `git rev-list --first-parent HEAD` (it reached HEAD only through a merge's side parent);
   - it is not a key of `SECURITY_REVIEWED`;
   - its blob is not the template on disk.

   Also assert the pair cancels out: the template blob at `c5ae8582^` (its first parent) equals the blob at `ee45c106`. Checked 2026-11-15: both are blob `57d7c82a…` (sha256 `e2291065…`), and neither commit is in `git rev-list --first-parent origin/dev`. These tests need history, so they skip on a shallow clone like the existing one. Done when the three test files in Verification pass.
4. @security: review the exclusion and the security column. Confirm that no excluded commit is the only record of a released version. Confirm that ee45c106 being unflagged is right. Done when a clearance row exists (decision log or the plan CSV notes).
5. @code-hygiene, then @conflict-auditor: audit the diff, and the CHANGELOG line if one is added. Done when both pass.
6. Operator (with @git-operations): commit, push, merge into dev with `--no-ff` behind §G. Then remove the worktree and branch. Done when the test passes in the §G local gate run with `AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1` and no deselect (not "on dev CI": CI most likely skips it).
7. @orchestrator (or the agent that ran the gate): the governance logs are append-only, so do not edit records 731 or 697 in place. Append one new remediation-log row that cross-references records 731 (and 697 if touched) and the merge sha. Write it with `agentteams.governance_logs.append_row`, imported from an origin/dev copy of the module until the `append_row` LF-tail fix (dev f9b8b674) is released: main's copy adds an empty record after a bare-LF row. Leave the old rows' `status` for a later pass (Rule 13). Done when `check_log` reports no problems and the new row is the last record.

| step | agent | action | inputs | outputs | status | notes | depends_on |
|---|---|---|---|---|---|---|---|
| 1 | operator | create worktree fix/lineage-branch-only off origin/dev | origin/dev | ../agentteams-wt/lineage-branch-only | pending | full history required | |
| 2 | @pipeline-core-expert | add BRANCH_ONLY exclusion for c5ae8582 and ee45c106 | scripts/build_launcher_lineage.py | builder change; --check exits 0 | pending | JSON must stay byte-identical | 1 |
| 3 | @test-suite-expert | add BRANCH_ONLY assertions: ancestor of HEAD, not in rev-list --first-parent HEAD, not in SECURITY_REVIEWED, blob not on disk; blob at c5ae8582^ equals blob at ee45c106 | tests/test_launcher_lineage.py | test change | pending | Rule 12: the list must not hide a mainline commit | 2 |
| 4 | @security | review exclusion and security column | diff of 2-3 | clearance record | pending | Rule 12 audit of the exclusion | 3 |
| 5 | @code-hygiene;@conflict-auditor | audit diff and docs | diff | audit pass | pending | | 4 |
| 6 | operator;@git-operations | commit, push, --no-ff merge into dev behind §G, remove worktree | branch | dev merge | pending | via sandbox_handoff; done when the test passes in the §G run with AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1, no deselect | 5 |
| 7 | @orchestrator | append a remediation-log row citing records 731/697 and the merge sha | merge sha | new log row | pending | append-only, no in-place edit; append_row from an origin/dev copy until f9b8b674 is released; do not flip status (Rule 13) | 6 |

**Verification** (in the worktree):
```
python3 scripts/build_launcher_lineage.py --check; echo $?          # 0
AGENTTEAMS_REQUIRE_LINEAGE_HISTORY=1 python3 -m pytest tests/test_launcher_lineage.py tests/test_mac_sandbox_augment.py tests/test_integrity_wasm_pins.py -q
git diff --stat origin/dev -- agentteams/data/launcher_lineage.json references/enforcement-integrity.json   # empty
python3 -m agentteams --verify-integrity
```
**Rollback.** Before the merge, delete the branch. After it, `git revert -m 1 <merge-sha>` on dev. No trust root is touched, so no re-pin is needed either way.

**Files likely touched:** `scripts/build_launcher_lineage.py`, `tests/test_launcher_lineage.py`, optionally `CHANGELOG.md`.
**Must NOT be touched:** `agentteams/data/launcher_lineage.json`, `references/enforcement-integrity.json`, `agentteams/templates/universal/sandbox/confine-run.sh`, `tests/test_mac_sandbox_augment.py`, `tests/test_integrity_wasm_pins.py`, `agentteams/launcher_lineage.py`. Any of these changing means the fix went wrong; stop and escalate.

## Open questions for the operator
- Do you accept a named exclusion list, or do you want a generic mainline-aware walk? The generic walk is not designed yet. It would likely rewrite the committed JSON and so need a manifest re-pin with @security clearance (unverified).
- Did any team render a launcher from the feature branch at `706e0f7b…`? If so, it will read `modified`.
- Should the confine-run.sh pin-chain checker from record 697 also check that the newest lineage entry equals the template, so a bad rebuild fails at authoring time?
