# Code-Hygiene Rules — Mechanization Status

> **Status: judgment, not specification.** Classified 2026-07-29. Each entry
> below is an assessment of whether a rule's decision procedure *can* be stated
> in full, not a commitment that it will be, nor a contract any tool implements.
> Re-derive it rather than trusting it if the rule catalogue changes.
>
> **Scope: this repository only.** Every surface named below is an agentteams
> internal (`agentteams/audit.py`, `tests/test_code_hygiene.py`,
> `agentteams.architecture`). A generated team has none of them, so this file is
> a record of where *agentteams'* mechanization stops — not a document to emit.
> It lived in `agentteams/templates/domain/` until 2026-07-29, where it was the
> only domain template no output plan reached; see
> `tests/test_template_emission_coverage.py`.

## Why this file exists

The catalogue declares 28 `CH-` rules. When this file was first written, two were
mechanized (`CH-14`, `CH-20`, both in `agentteams/audit.py`) and nothing recorded
which of the remaining 26 were judgment **by necessity** and which were merely
**unwritten** — so there was no way to tell a deliberate boundary from a backlog.
Six are fully mechanized as of 2026-07-29 and five partly; the table below is the
current state.

That distinction is the descent condition: work belongs with a procedure once its
decision procedure can be specified, and belongs with an agent when it cannot.
A rule mechanized too early is worse than one left alone, because a passing check
reads as conformance and suppresses the judgment that was actually required.

## Classification

| Rule | Status | Reason |
|---|---|---|
| CH-01 No backup files in source tree | **mechanized** | `tests/test_code_hygiene.py::test_ch01_no_backup_files_tracked` — tracked paths ending `.bak`/`~`/`.orig`/`.rej`. Says nothing about untracked working copies. |
| CH-02 Script lifecycle | judgment | Requires knowing whether a script is still wanted. No artifact records intent. |
| CH-03 No ad-hoc scripts in output dir | judgment | **Reclassified 2026-07-29.** The old reason — "executable/script extensions in a known directory" — describes a *different, weaker* rule: CH-03 forbids **ad-hoc** scripts (investigative, debug, fix, benchmark) while permitting production code, so it turns on *intent*. That is the same information gap that makes CH-02 and CH-27 judgment, and no artifact records it. |
| CH-04 Debug artifacts gitignored | **mechanizable** | Cross-check known debug patterns against `.gitignore`. **Missing definition (verified 2026-07-29): the package has no debug-artifact pattern vocabulary at all.** Writing one here would be inventing the rule's content, which the standing caution forbids. |
| CH-05 Single source of truth for mappings | **partly mechanized** | `tests/test_code_hygiene.py::test_framework_registry_has_single_source` — asserts exactly one module defines `FRAMEWORKS`/`_ADAPTERS` as a dict literal. **Scope: one named mapping.** Detecting that two *arbitrary* structures mean the same mapping is semantic and remains judgment. |
| CH-06 Commands ≤5 lines, no heredocs | **partly mechanized** | Two halves, unequally enforced. `tests/test_code_hygiene.py::test_ch06_no_inline_heredocs_in_agent_instructions` — **0 violations, a clean PASS means the rule holds**. `::test_ch06_long_command_blocks_do_not_increase` — a **ratchet** over 10 pre-existing long blocks: a PASS means no *new* one, and adjudicates none of the ten. Scope: shell fences in `agentteams/templates/**`. |
| CH-07 Standard module structure | **partly mechanized** | `tests/test_code_hygiene.py::test_no_new_oversized_modules` (+ `test_length_allowlist_has_no_stale_entries`) — the **size** dimension only, as a ratchet with an allowlist. A PASS means "no *new* module exceeds the ceiling", not that module structure is standard. Section presence and order remain unwritten. |
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
| CH-22 Type check inputs | **partly mechanized** | `tests/test_code_hygiene.py::test_refactor_modules_are_fully_type_annotated` — full annotation coverage, but **scoped to a fixed module list** (`_REFACTOR_MODULES`). Says nothing about the rest of the package, and annotation presence is not input checking. |
| CH-23 Fail fast on invalid inputs | judgment | Requires knowing which inputs are invalid. |
| CH-24 Exceptions as last resort | **partly mechanized** | `tests/test_code_hygiene.py::test_broad_except_does_not_increase` and `::test_swallowed_exceptions_do_not_increase` — AST counts of broad and swallow-only handlers, **ratchets against a baseline**. A PASS means no handler was *added*; it adjudicates none of the existing ones. Whether a given condition could have been encoded explicitly stays a design assessment. |
| CH-25 Screen against bad-habits catalogue | partly mechanizable | `ai_bad_habits.py` holds the catalogue; matching it is pattern work, but a match is a prompt for review rather than a verdict. |
| CH-26 Least authority in tool declarations | **mechanizable** | Declared tools against a role's required set; `audit.py::_check_readonly_tool_declarations` already does the read-only case. |
| CH-27 Long-lived utilities over ad-hoc scripts | judgment | Same intent problem as CH-02. |
| CH-28 Minimal, scoped edits | judgment | A property of a change, not of a tree. Not checkable from a snapshot. |

## Summary

Two independent axes are folded into one column, so the vocabulary distinguishes
them by suffix: **-ed = a check exists**, **-able = a check could exist**.

| Status | Count | Means |
|---|---|---|
| mechanized | 6 | A check exists and covers the rule |
| partly mechanized | 5 | A check exists and covers **part** of the rule; the row says which part |
| mechanizable | 3 | No check; the decision procedure is fully specifiable |
| partly mechanizable | 5 | No check; only part of the decision procedure is specifiable |
| judgment | 9 | No check is possible; the information is not in what a checker can see |

These counts are **derived from the table by
`tests/test_code_hygiene.py::test_mechanization_summary_counts_match_the_table`**,
not tallied by hand. The first hand-written summary reported eleven judgment rules
where the table held ten — the same defect this file exists to expose — so the
tally is no longer trusted to a human.

**Updated 2026-07-29 (second pass).** CH-01, CH-11, CH-13 and CH-15 moved from
*mechanizable* to *mechanized*, and CH-18 to *judgment* after an attempt showed
its semantics are not settled. Then a re-read of every test in
`tests/test_code_hygiene.py` found **four rules misfiled**: CH-05 and CH-24 were
listed as *judgment* and CH-07 and CH-22 as *unwritten backlog*, while each in
fact had an enforcing test. The error ran one way — **overstating the backlog and
understating coverage** — which is the direction that wastes work, because a
reader planning mechanization would have built checks that already existed.

Filing those four then exposed a defect in the vocabulary itself. `partly
mechanizable` had been carrying two unrelated meanings: CH-09/10/16/19/25 use it
for "only partly *specifiable*, nothing built", while the four new rows meant
"built, but narrower than the rule". `partly mechanized` was split out so the
suffix carries the distinction. The count that was briefly stated as nine partly
mechanizable was that conflation, and the surface-citation test below is what
surfaced it — five rows claimed coverage while naming no implementing surface.

## What the classification shows

**9 rules are judgment by necessity**, and they cluster: CH-02, **CH-03**, CH-21,
CH-27 and CH-28 all turn on *intent* or *history* that no snapshot of the tree
records.
No amount of implementation effort reaches them, because the information is not
present in what a checker can see.

**Three remain mechanizable and unwritten** — CH-04, CH-17, CH-26 — and each names
the specific definition it lacks. `CH-04`: the package has **no** debug-artifact
pattern vocabulary, so writing one would be inventing the rule's content. `CH-17`:
standard linters already encode import ordering, so the correct implementation is
*adopting a linter rule*, which is a tooling decision rather than a check to
hand-roll. `CH-26` is closest to ready — it extends a check `audit.py` already
performs for read-only tools — but needs a role → required-tools mapping that does
not exist.

**Two left the backlog on 2026-07-29 by being examined rather than implemented.**
`CH-06` was implemented, but only half cleanly, so it is *partly* mechanized.
`CH-03` was **reclassified to judgment**: its stated reason described a weaker rule
than the catalogue states, and the real rule turns on intent. Shrinking a backlog by
finding that an entry was misfiled is the same correction as growing it by finding a
shipped check — both are the classification describing itself accurately.

**All five partly mechanized rules are ratchets or scoped guards.** CH-05 covers
one named mapping; CH-06 enforces its heredoc half outright but ratchets its length
half over 10 pre-existing blocks; CH-07 covers size but not structure; CH-22 covers
a fixed module list; CH-24 counts handlers against a baseline. Each PASS is narrower than
its rule, and the row states how. **Five more are partly mechanizable** — CH-09,
CH-10, CH-16, CH-19, CH-25 — which is a different claim: nothing is built, and
only part of the procedure could be.

**Two rules have tests that name them and mechanize nothing.**
`test_extension_rules_present_in_both_templates` and
`test_ch28_constraints_sentence_present` assert that CH-26 and CH-28 *appear in
the rule templates*. Those are **CH-20 parity guards** — they stop the agent
summary and the enforcement catalogue drifting apart — and crediting them to
CH-26 or CH-28 would be precisely the error this file was written to expose:
reading a PASS as more than it established. Both rules therefore stay where they
were.

**CH-20's mechanization is narrower than its rule.** The rule forbids agent docs
contradicting each other; the check finds duplicate descriptions. Duplication is
evidence of one kind of contradiction and misses every other kind. **A team
reading `CH-20: PASS` may conclude more than the check established** — which is
the exact hazard this classification exists to make visible.

## Standing caution

**`mechanizable` is a claim, not a finding.** Each entry asserts that a rule's
judgment is fully specifiable, and each such assertion is an invitation to build a
check that reports conformance it did not verify. Nothing here should be
implemented without re-examining, at that time, what the resulting PASS would
actually mean.

**A ratchet is not a conformance check.** CH-07 and CH-24 are enforced by counts
measured against a baseline: the check fails when the count *rises*. That is
genuinely useful — it stops regression — but a green ratchet establishes only
"nothing got worse". It says nothing about the violations already present, and the
allowlist and baselines are the record of those. `LENGTH_ALLOWLIST` currently
carries entries; `BROAD_EXCEPT_BASELINE` and `SWALLOW_BASELINE` are non-zero.
Reading `CH-07: PASS` as "modules are well structured" inverts what was measured.

**A scoped check is not a general one.** CH-05 guards one mapping and CH-22 one
module list. Both would pass unchanged if a second registry or an unannotated
module appeared outside their scope. The scope is stated in each row because it
cannot be recovered from the status alone.
