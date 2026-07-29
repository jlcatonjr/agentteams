# Code-Hygiene Rules — Mechanization Status

> **Status: judgment, not specification.** Classified 2026-07-29. Each entry
> below is an assessment of whether a rule's decision procedure *can* be stated
> in full, not a commitment that it will be, nor a contract any tool implements.
> Re-derive it rather than trusting it if the rule catalogue changes.

## Why this file exists

The catalogue declares 28 `CH-` rules. When this file was first written, two were
mechanized (`CH-14`, `CH-20`, both in `agentteams/audit.py`) and nothing recorded
which of the remaining 26 were judgment **by necessity** and which were merely
**unwritten** — so there was no way to tell a deliberate boundary from a backlog.
Six are mechanized as of 2026-07-29; the table below is the current state.

That distinction is the descent condition: work belongs with a procedure once its
decision procedure can be specified, and belongs with an agent when it cannot.
A rule mechanized too early is worse than one left alone, because a passing check
reads as conformance and suppresses the judgment that was actually required.

## Classification

| Rule | Status | Reason |
|---|---|---|
| CH-01 No backup files in source tree | **mechanized** | `tests/test_code_hygiene.py::test_ch01_no_backup_files_tracked` — tracked paths ending `.bak`/`~`/`.orig`/`.rej`. Says nothing about untracked working copies. |
| CH-02 Script lifecycle | judgment | Requires knowing whether a script is still wanted. No artifact records intent. |
| CH-03 No ad-hoc scripts in output dir | **mechanizable** | Executable/script extensions in a known directory. |
| CH-04 Debug artifacts gitignored | **mechanizable** | Cross-check known debug patterns against `.gitignore`. |
| CH-05 Single source of truth for mappings | judgment | Detecting that two structures *mean* the same mapping is semantic. |
| CH-06 Commands ≤5 lines, no heredocs | **mechanizable** | Line counting and heredoc syntax in fenced blocks. |
| CH-07 Standard module structure | **mechanizable** | Presence and order of required sections, as `audit.py` already does for agent files. |
| CH-08 Common utilities over duplication | judgment | Requires deciding that two implementations are the same thing. Threshold-based approximations misfire on parallel-but-distinct code. |
| CH-09 Config values in config files | partly mechanizable | Literal constants in code are detectable; whether a literal *is* configuration is not. |
| CH-10 Dead code removal | partly mechanizable | Unreferenced symbols are computable; dynamic dispatch and public API make "dead" undecidable in general. |
| CH-11 Tests in dedicated directory | **mechanized** | `tests/test_code_hygiene.py::test_ch11_tests_live_in_the_tests_directory` — every tracked `test_*.py` under `tests/`. Does not check the tests are good or run. |
| CH-12 Purposeful package init files | judgment | "Purposeful" is the whole content of the rule. |
| CH-13 No circular imports | **mechanized** | `architecture.detect_import_cycles` over `module_level_edges`, guarded by `tests/test_living_doc_and_cycles.py::test_this_package_has_no_load_time_cycles`. Covers *load-time* static imports of one package: a deferred, dynamic or third-party cycle is not modelled. |
| CH-14 Docs reference code, don't duplicate | **mechanized** | `audit.py::_check_ch14_inline_data_blocks` — consecutive-row threshold outside Invariant Core. |
| CH-15 No legacy dirs in source | **mechanized** | `tests/test_code_hygiene.py::test_ch15_no_legacy_directories_in_source` — `oldScripts`/`legacy`/`deprecated` only. Superseded code under any other name is invisible to it. |
| CH-16 Temp files cleaned after use | partly mechanizable | Presence is detectable; whether a file is still in use is not. |
| CH-17 Import grouping and ordering | **mechanizable** | Syntactic; standard linters already encode it. |
| CH-18 Version-numbered files are branches | judgment | **Attempted and rejected 2026-07-29.** A naive version-token probe flags dated work summaries (`2026-05-04.md`) as version-numbered siblings. Distinguishing a version suffix from a date requires knowing which is which. |
| CH-19 Screenshot retention | partly mechanizable | Age and location are computable; whether an image is still cited needs a reference scan. |
| CH-20 Agent docs must not contradict | **mechanized** | `audit.py::_check_ch20_duplicate_descriptions`. **Note:** the check covers duplicate descriptions only — a narrow proxy for contradiction, which is otherwise judgment. |
| CH-21 Validate features before mainline | judgment | Concerns process history, not tree state. |
| CH-22 Type check inputs | **mechanizable** | Annotation presence on public signatures. |
| CH-23 Fail fast on invalid inputs | judgment | Requires knowing which inputs are invalid. |
| CH-24 Exceptions as last resort | judgment | Whether a condition *could* have been encoded explicitly is a design assessment. |
| CH-25 Screen against bad-habits catalogue | partly mechanizable | `ai_bad_habits.py` holds the catalogue; matching it is pattern work, but a match is a prompt for review rather than a verdict. |
| CH-26 Least authority in tool declarations | **mechanizable** | Declared tools against a role's required set; `audit.py::_check_readonly_tool_declarations` already does the read-only case. |
| CH-27 Long-lived utilities over ad-hoc scripts | judgment | Same intent problem as CH-02. |
| CH-28 Minimal, scoped edits | judgment | A property of a change, not of a tree. Not checkable from a snapshot. |

## Summary

| Status | Count |
|---|---|
| mechanized | 6 |
| mechanizable | 7 |
| partly mechanizable | 5 |
| judgment | 10 |

Updated 2026-07-29: CH-01, CH-11, CH-13 and CH-15 moved from *mechanizable* to
*mechanized*; CH-18 moved to *judgment* after an attempt showed its semantics are
not settled. The counts above are now derived from the table rather than tallied
by hand — the first hand-written summary reported eleven judgment rules where the
table held ten, which is the same defect this file was written to expose.

## What the classification shows

**10 rules are judgment by necessity**, and they cluster: CH-02, CH-21, CH-27
and CH-28 all turn on *intent* or *history* that no snapshot of the tree records.
No amount of implementation effort reaches them, because the information is not
present in what a checker can see.

**Seven remain mechanizable and unwritten** — CH-03, CH-04, CH-06, CH-0Seven,
CH-1Seven, CH-22, CH-26. These are a genuine backlog rather than a boundary. The four that
shipped (CH-01, CH-11, CH-13, CH-15) were selected on one criterion: what a PASS
establishes is unambiguous. The remainder each need a definition the
classification does not supply — which debug patterns count, what makes a literal
*configuration*, what tool set a role *requires*. `CH-26` is closest: it extends a
check `audit.py` already performs for read-only tools, but needs a role → required
tools mapping that does not exist.

**CH-20's mechanization is narrower than its rule.** The rule forbids agent docs
contradicting each other; the check finds duplicate descriptions. Duplication is
evidence of one kind of contradiction and misses every other kind. **A team
reading `CH-20: PASS` may conclude more than the check established** — which is
the exact hazard this classification exists to make visible.

## Standing caution

The `mechanizable` column is the dangerous one. Each entry is a claim that a
rule's judgment is fully specifiable, and each such claim is an invitation to
build a check that reports conformance it did not verify. Nothing here should be
implemented without re-examining, at that time, what the resulting PASS would
actually mean.
