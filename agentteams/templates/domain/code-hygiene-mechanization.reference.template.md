# Code-Hygiene Rules — Mechanization Status

> **Status: judgment, not specification.** Classified 2026-07-29. Each entry
> below is an assessment of whether a rule's decision procedure *can* be stated
> in full, not a commitment that it will be, nor a contract any tool implements.
> Re-derive it rather than trusting it if the rule catalogue changes.

## Why this file exists

The catalogue declares 28 `CH-` rules. Two are mechanized in `agentteams/audit.py`
(`CH-14`, `CH-20`). Nothing recorded which of the remaining 26 are judgment **by
necessity** and which are merely **unwritten** — so there was no way to tell a
deliberate boundary from a backlog.

That distinction is the descent condition: work belongs with a procedure once its
decision procedure can be specified, and belongs with an agent when it cannot.
A rule mechanized too early is worse than one left alone, because a passing check
reads as conformance and suppresses the judgment that was actually required.

## Classification

| Rule | Status | Reason |
|---|---|---|
| CH-01 No backup files in source tree | **mechanizable** | Filename patterns (`*.bak`, `*~`, `*.orig`) against a path list. Fully decidable. |
| CH-02 Script lifecycle | judgment | Requires knowing whether a script is still wanted. No artifact records intent. |
| CH-03 No ad-hoc scripts in output dir | **mechanizable** | Executable/script extensions in a known directory. |
| CH-04 Debug artifacts gitignored | **mechanizable** | Cross-check known debug patterns against `.gitignore`. |
| CH-05 Single source of truth for mappings | judgment | Detecting that two structures *mean* the same mapping is semantic. |
| CH-06 Commands ≤5 lines, no heredocs | **mechanizable** | Line counting and heredoc syntax in fenced blocks. |
| CH-07 Standard module structure | **mechanizable** | Presence and order of required sections, as `audit.py` already does for agent files. |
| CH-08 Common utilities over duplication | judgment | Requires deciding that two implementations are the same thing. Threshold-based approximations misfire on parallel-but-distinct code. |
| CH-09 Config values in config files | partly mechanizable | Literal constants in code are detectable; whether a literal *is* configuration is not. |
| CH-10 Dead code removal | partly mechanizable | Unreferenced symbols are computable; dynamic dispatch and public API make "dead" undecidable in general. |
| CH-11 Tests in dedicated directory | **mechanizable** | Path convention. |
| CH-12 Purposeful package init files | judgment | "Purposeful" is the whole content of the rule. |
| CH-13 No circular imports | **mechanizable** | Import-graph cycle detection. `architecture.py` already builds the graph. |
| CH-14 Docs reference code, don't duplicate | **mechanized** | `audit.py::_check_ch14_inline_data_blocks` — consecutive-row threshold outside Invariant Core. |
| CH-15 No legacy dirs in source | **mechanizable** | Directory-name patterns. |
| CH-16 Temp files cleaned after use | partly mechanizable | Presence is detectable; whether a file is still in use is not. |
| CH-17 Import grouping and ordering | **mechanizable** | Syntactic; standard linters already encode it. |
| CH-18 Version-numbered files are branches | **mechanizable** | Filenames differing only by a version token. |
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
| mechanized | 2 |
| mechanizable | 11 |
| partly mechanizable | 5 |
| judgment | 10 |

## What the classification shows

**Ten rules are judgment by necessity**, and they cluster: CH-02, CH-21, CH-27
and CH-28 all turn on *intent* or *history* that no snapshot of the tree records.
No amount of implementation effort reaches them, because the information is not
present in what a checker can see.

**Eleven are mechanizable and unwritten** — mostly syntactic or path-based
(CH-01, CH-03, CH-04, CH-06, CH-11, CH-15, CH-17, CH-18, CH-22). These are a
genuine backlog rather than a boundary. Two of them already have most of their
machinery: `CH-13` needs the import graph `architecture.py` builds, and `CH-26`
extends a check `audit.py` already performs for read-only tools.

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
