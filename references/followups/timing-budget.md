# Follow-up: Load-sensitive timing tests in the local gate's full run

**Status:** open (written 2026-11-15) · **Source:** remediation log line 733 / record 732 and line 734 / record 733 (that log is local and gitignored; both rows are quoted below) · **Suggested owner:** @test-suite-expert (design), @primary-producer (edit), @security (review of the length-cap test change), @git-operations (merge)

Quoted log text (row dated 2026-11-14, category `ci`, status `open`): "Flaky timing budget: tests/test_wasm_agent_e2e.py::test_r13_step_time[verified-copy] measured 6.49s against a 6.0s budget during a full local gate run (70 min, machine under load). Consider a load-tolerant budget or marking it a perf test outside the local gate. Also: the .venv-ci local-gate env carries cryptography 50.0.0 below the 50.0.2 pin, so test_installed_version_matches_pin_when_present fails locally (see the 2026-11-14 anaconda cryptography row)."

Second row, line 734 / record 733 (dated 2026-11-14, category `ci`, status `open`): "Second load-timing flake in the full local gate: tests/test_constitutional_gate_hook.py::test_every_pattern_is_bounded_at_the_length_caps[git ] took 16.5s against its 15s budget during a ~57-minute run (it passed in the previous run and in isolation). With test_r13_step_time this makes the section-G local gate nondeterministic on a loaded workstation. Consider running timing-budget tests in a separate, serial perf step, or scaling budgets by a measured baseline."

**The cryptography half is resolved.** On 2026-11-15 an operator handoff upgraded `.venv-ci` to cryptography 50.0.2, and `tests/test_supply_chain_pins.py` passed. Do not redo it. This follow-up covers the timing half only.

## Report

**What is wrong.** Two tests assert wall-clock budgets. Under the load of a full local gate run (56 to 70 minutes), they can exceed them. The §G gate in `references/git-procedures.md` runs `python -m pytest tests/ -q` with no provision for this. So agents deselect the two tests from the full run and then run them alone. That workaround is undocumented. Only the two local log rows above mention the failures, and nothing tracked in the repo records the deselection.

**Evidence** (read on `origin/dev` at `33824c72`):
- `tests/test_wasm_agent_e2e.py::test_r13_step_time[verified-copy]` (added `6ef690b9`). It runs 6 host steps through a real `wasm_pins.PinnedResolver`, takes the median of `step_timings[*].total_s`, and asserts `median <= 6.0`. With `AGENTTEAMS_WASM_R13_GATE=1` the limit is 2.0. It is skipped unless the dev wasmtime and the dev CPython-WASI are present (`@needs_wasmtime`, `@needs_dev_python`). The full-run failure measured 6.49 s. Run alone on 2026-11-15: "steps=6 median=2.436s min=2.404s max=2.522s".
- `tests/test_constitutional_gate_hook.py::test_every_pattern_is_bounded_at_the_length_caps[git ]` (added `b4e05520`, heads extended in `bb30b8c4` on 2026-11-14). The full-run failure took 16.5 s against 15 s in a ~57-minute run (record 733). It pipes 4 lines of 8180 chars to `agentteams/templates/universal/hooks/constitutional-gate.py` in a subprocess and asserts a run time under 15 s. It then checks that an over-cap command gets an `ask` decision with reason "too long to check".
- **Security intent (confirmed).** This is a ReDoS guard. The docstring and the hook's comment above `_MAX_LINE = 8192` say it: the old `git push` patterns were cubic ("`git push ` x2000 took 89 s"), and "a timed-out hook lets the command run". Sibling guards with the same intent: `test_merge_pattern_has_no_catastrophic_backtracking` (< 10 s) and `test_gate_stays_fast_on_padded_commands` (< 10 s).
- Headroom I measured on 2026-11-15 (a copy of the `origin/dev` hook run from `$TMPDIR`, python3 3.14.6, the [git ] input): `git ` 5.98 s, `gh ` 5.28 s, `git push ` 3.94 s. So the `git ` case has about 2.5x headroom when idle. The hook comment says "a few seconds on python3 3.9".
- Both tests passed alone on 2026-11-15 after a 56-minute full run ("2 passed in 21.25s").
- Where else the deselection appears: nowhere tracked. A grep of the working tree (minus venvs, `.git` and the gitignored remediation log) and of `git log --all` messages finds the two ids only in their own test files. No CI workflow, script or doc names them.
- Marker infrastructure: none. `pyproject.toml` `[tool.pytest.ini_options]` holds only `testpaths = ["tests"]`. There is no `pytest.ini`, `setup.cfg`, `tox.ini` or registered marker. `tests/conftest.py` defines `pytest_sessionstart`, `pytest_runtest_teardown` and `pytest_sessionfinish`, but no collection hook.
- CI: `.github/workflows/ci.yml` runs `pytest tests/` (line 74) and runs the live WASM files via `scripts/wasm_live_ci.py` (whose `LIVE_FILES` includes `tests/test_wasm_agent_e2e.py`). It already treats R13 timing as "logged, not gated" (lines 295, 364).
- **Trap for any `-m` or `--deselect` design.** `tests/test_os_sandbox_product_enforcement.py::_whole_module_selected` returns False whenever `-k`, `-m`, `--lf`, `--deselect` or a node id is used. When that happens it skips writing the itest tripwire record (only when `RUN_CLAUDE_SANDBOX_ITEST=1`). The current ad-hoc `--deselect` already suppresses that record. Adding `-m "not perf"` to the full run would do the same. A skip added at collection time would not (that function checks options only).

**Known vs not yet known.** Known: the budgets, how they measure, that both pass alone, and that the R13 failure was load-induced. The `[git ]` failure took 16.5 s against 15 s (record 733), so it overshot by 10%, against the R13 overshoot of 8% (6.49 s against 6.0 s). Not yet known: whether the full run uses `pytest-xdist` or other parallelism (unverified); the harness hook timeout this guard protects against (unverified; Claude Code's default is believed to be 60 s).

**Risk of leaving it.** The gate stays red by accident, or agents keep deselecting by hand. That habit could also drop a real ReDoS regression in the gate hook without anyone noticing, and the deselection already silently suppresses the itest tripwire record.

## Plan

**Options.**
- A. A `perf` marker, with the full run using `-m "not perf"` and a second run using `-m perf`. Simple. But it trips `_whole_module_selected`, so the full run would never write the tripwire record.
- B. A `perf` marker that `tests/conftest.py` turns into a skip when `AGENTTEAMS_PERF=defer`, plus a second gate command `python -m pytest tests/ -q -m perf` on an otherwise idle machine. Same split as A, with no option-based filtering in the full run. CI runs `pytest tests/` unchanged, so CI still runs every perf test.
- C. A load-tolerant budget: time a fixed CPU calibration loop and scale the budget by the observed slowdown, capped (for example at 3x). No gate change, but it adds noise. A slow regression could also hide inside the scaled budget. For the ReDoS guard, a cubic blow-up (89 s) would still fail, but a quadratic one might pass.
- **Recommendation: B.** Keep both budgets exactly as they are: 6.0 s / 2.0 s for R13, and 15 s for the length cap. Mark the whole length-cap test perf, plus its two 10 s siblings, so the ReDoS guards always run together in the second command. Never use `--deselect`.
- **Rule 12: what the defer switch newly permits.** With `AGENTTEAMS_PERF=defer` set, the ReDoS guards skip. Left alone, that would let a run go green without them, and a workflow that set the variable would drop them from CI. Three guards close this. (1) A deferred run prints a loud terminal-summary line that names every deferred test, so a skip is never silent. (2) A test asserts that no workflow under `.github/workflows/` sets `AGENTTEAMS_PERF`. (3) §G states that the `-m perf` run is required before a merge, not optional.

**Preconditions and constraints.** Work in a worktree off `origin/dev` under `../agentteams-wt/` (`git worktree add --no-track ../agentteams-wt/perf-marker -b chore/perf-marker origin/dev`). Merge into `dev` with `--no-ff` behind the §G local gate. In the Claude Code sandbox an agent cannot add or remove worktrees, commit or push. Those go to the operator through `python3 -m agentteams.sandbox_handoff write`. The gate hook is a security surface, so @security reviews the change before merge. Do not edit the hook or its budgets. If the integrity manifest covers test files (unverified), regenerating it is a trust-root step and needs @security clearance first.

1. **Operator** (handoff): create the worktree and branch. Done when the worktree exists at `origin/dev`.
2. **@primary-producer**: register the marker in `pyproject.toml`: `markers = ["perf: wall-clock budget test; deferred from the loaded full run, run alone (§G)"]`. Done when `pytest --markers` lists `perf`.
3. **@primary-producer**: in `tests/conftest.py`, add `pytest_collection_modifyitems`. When `AGENTTEAMS_PERF=defer`, it adds `pytest.mark.skip(reason="perf: deferred to the §G perf run")` to each `perf` item. Docstring per the style rules. Also add `pytest_terminal_summary`: when any test was deferred, it prints one line such as `AGENTTEAMS_PERF=defer: N perf tests DEFERRED, run "python -m pytest tests/ -q -m perf" before merging:` followed by each node id. Done when a deferred run reports those tests as skipped and prints that line.
4. **@primary-producer**: add `@pytest.mark.perf` to `test_r13_step_time`, `test_every_pattern_is_bounded_at_the_length_caps`, `test_merge_pattern_has_no_catastrophic_backtracking` and `test_gate_stays_fast_on_padded_commands`. Change no budget or assertion. In `tests/test_conftest_perf_guard.py` (name to confirm), add a test that reads every file under `.github/workflows/` and fails if any sets `AGENTTEAMS_PERF`. Done when the diff to the four tests shows only marker lines and the new test passes.
5. **@primary-producer**: in `references/git-procedures.md` §G, replace the first gate line with `AGENTTEAMS_PERF=defer python -m pytest tests/ -q`, then add `python -m pytest tests/ -q -m perf   # REQUIRED: alone, machine otherwise idle`. Add two sentences: the `-m perf` run is required before every merge into `dev`, not optional, because the first command skips the ReDoS guards; and never `--deselect` a perf test, because that also suppresses the itest tripwire record. Done when §G shows both commands and the "required" wording.
6. **@security**: confirm the ReDoS guards still run in the gate and in CI, with unchanged bounds, and that the three Rule 12 guards (summary line, workflow test, "required" wording) are in place. Done when PASS is recorded.
7. **@test-suite-expert + @conflict-auditor**: review, and check that no other doc quotes the old single gate command (`git grep -n "pytest tests/ -q"`). In `references/git-procedures.md` that grep hits more than §G (origin/dev 7563b693): line 15 (§A.1 pre-commit checklist, item 1), line 242 (§B.5 step 7, post-resolution verification), line 254 (§B.6 post-integration verification) and line 516 (§G). The edit must either update §A.1, §B.5 and §B.6 too, or skip them on purpose with a recorded reason (for example, that they are quick checks, not the merge gate). Done when there are no findings or they are fixed.
8. **@git-operations / operator**: commit, run the §G gate, merge `--no-ff` into `dev`, push. Then append one new remediation-log row that cross-references records 732 and 733 and the merge sha (Rule 13; the old rows are not edited and their status stays `open`), using `append_row` imported from an `origin/dev` copy until the `f9b8b674` fix is released (see `append-row-release.md`). Done when `dev` holds the merge.

| step | agent | action | inputs | outputs | status | notes | depends_on |
|---|---|---|---|---|---|---|---|
| 1 | operator | create worktree chore/perf-marker off origin/dev | sandbox_handoff | ../agentteams-wt/perf-marker | pending | sandbox cannot add worktrees | |
| 2 | primary-producer | register perf marker | pyproject.toml | pyproject.toml | pending | | 1 |
| 3 | primary-producer | conftest skip when AGENTTEAMS_PERF=defer, plus terminal-summary line naming deferred tests | tests/conftest.py | tests/conftest.py | pending | collection-time skip, not -m; skip must be loud (Rule 12) | 2 |
| 4 | primary-producer | mark 4 timing tests perf; add test that no workflow sets AGENTTEAMS_PERF | two test files; .github/workflows/ | two test files; new test | pending | no budget changes | 2 |
| 5 | primary-producer | split §G gate into deferred + required perf runs | references/git-procedures.md | same | pending | -m perf run is required, not optional; warn against --deselect | 3;4 |
| 6 | security | review ReDoS guard coverage and the Rule 12 guards | diff | PASS/HALT | pending | gate hook is a security surface | 5 |
| 7 | test-suite-expert | review + conflict-auditor sweep | diff | findings | pending | grep also hits §A.1, §B.5, §B.6: cover or skip on purpose | 6 |
| 8 | git-operations | commit, gate, --no-ff merge to dev, Rule 13 note | branch | dev merge | pending | operator handoff in sandbox | 7 |

**Verification.**
- `python -m pytest --markers | grep perf`
- `AGENTTEAMS_PERF=defer python -m pytest tests/test_wasm_agent_e2e.py tests/test_constitutional_gate_hook.py -q -rs`: perf tests show as skipped, and the summary line names each one.
- `grep -rn AGENTTEAMS_PERF .github/workflows/`: no output (and the new workflow test passes).
- `python -m pytest tests/ -q -m perf`: the 4 tests run (R13 skips without the dev runtimes) and pass.
- `python -m pytest tests/ -q`, with no env var: everything runs, as in CI.

**Rollback.** Revert the merge commit on `dev` (`git revert -m 1 <merge>`). Nothing is renamed or deleted, and the budgets never change.

**Files likely touched:** `pyproject.toml`, `tests/conftest.py`, `tests/test_wasm_agent_e2e.py`, `tests/test_constitutional_gate_hook.py`, one new test file for the workflow check, `references/git-procedures.md`, `CHANGELOG.md` (if the repo convention requires an entry; unverified).
**Must NOT be touched:** `agentteams/templates/universal/hooks/constitutional-gate.py` (including `_MAX_LINE`/`_MAX_COMMAND`), any budget value, `.github/workflows/ci.yml`, `scripts/wasm_live_ci.py`, `references/enforcement-integrity.json` (unless @security clears a regeneration).

**Open questions for the operator.**
- Should the perf run be a separate gate line (proposed), or folded into one script?
- Should all four ReDoS and timing guards be marked perf, or only the two that failed?
- Is the 6.0 s non-gate R13 limit still wanted at all, given CI only logs R13?
