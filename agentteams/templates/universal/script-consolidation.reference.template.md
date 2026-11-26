<!--
SECTION MANIFEST
| section_id               | designation   |
|--------------------------|---------------|
| script_consolidation     | FENCED        |
| placement_decision_rule  | USER-EDITABLE |
-->

<!-- AGENTTEAMS:BEGIN script_consolidation v=1 -->
# Script Consolidation Reference — {PROJECT_NAME}

How this team reviews its one-off scripts and decides which recurring ones become maintained tools.
It supports the judgment that CH-08 (duplication) and CH-27 (prefer long-lived utilities) ask
`@code-hygiene` to make. It is a procedure for gathering evidence and writing a plan. It is not a
conformance check: nothing here reports a pass or a fail against either rule.

Run it when the operator asks for a script review, or when `@code-hygiene` finds the same one-off
logic written again.

## The one rule that prevents most wrong findings

**Every statement that a tool exists, is missing, or only partly covers a need is read from the
default branch, and says so.** A local checkout can lag the default branch, and so can a scheduled
job's pinned copy. A tool reported as missing may have been added last week.

Before judging coverage, run:

```
python -m agentteams.ref_branch <repository>
python -m agentteams.ref_branch <repository> --show <path/to/tool>
```

The first prints the reference ref, how long ago it was fetched, and how far the checkout is behind.
Put that line beside each coverage verdict as `Reference branch: <line>`. If the fetch is old, fetch
and re-check before reporting. Where the module is unavailable, read with
`git show origin/<default>:<path>` and record the ref and the fetch time by hand.

## Phases

| # | Phase | What it produces | Role |
|---|---|---|---|
| 0 | Plan | A plan and steps CSV under the plan rule | `@orchestrator` |
| 1 | Discover | An inventory of scripts outside the maintained tool folders, with every pruned folder counted by reason | `@navigator` |
| 2 | Mark copies | The inventory with snapshots, worktrees, vendored trees and byte-identical copies of tracked tools marked, so that only authored files are in scope | `@navigator` |
| 3 | Classify | A purpose family for each authored file | `@code-hygiene` |
| 4 | Fragments | Counts of the scaffolds that recur across families (connection set-up, read-only guards, path edits, dry-run switches) | `@code-hygiene` |
| 5 | Review | One reviewer per family group, each under the brief below | domain agents, read-only |
| 6 | Verify | Every high-impact claim re-read from the default branch | `@technical-validator` where the team has one, otherwise `@adversarial` |
| 7 | Placement | A home for each proposed tool, by the placement rule below | `@repo-liaison` |
| 8 | Report | Findings with excerpts quoted exactly and screened for sensitive identifiers | `@primary-producer` where the team has one, otherwise `@orchestrator` |
| 9 | Audit | An accuracy audit and a design audit (prompts below) | `@quality-auditor` where the team has one, and `@adversarial` |
| 10 | Consolidation plan | Waves of work: prerequisites, then the most-used low-risk tools, then safety-critical ones | `@orchestrator` |

Working rules for phases 1 to 4:

- Count what is skipped. A folder pruned without a count is a silent gap in the review.
- Give each column of a working table one meaning, and keep each step's output as a file. A step
  that rewrites its own input cannot be reproduced.
- Match statement shapes, not words. `UPDATE x SET` is a write; "update the plan" is prose.
- Scratch folders hold copies of whole repositories. Mark them before counting, or the count will
  be mostly copies.

## Reviewer brief (phase 5)

Give each reviewer its file list, the family labels (crude; regroup freely), and read-only access.
Ask for four things, in about 1,200 words:

1. **Recurring sub-purposes.** Four to eight, each with an approximate count, the span of weeks it
   covers, and how the copies differ: logic typed again, parameters changed only, or really
   different.
2. **Exemplar fragments.** One to three per sub-purpose, as exact `path:Lstart-Lend` ranges the
   reviewer has read. The report quotes them as given.
3. **Coverage by existing tools.** Covered (and why scripts bypass the tool), partial (what is
   missing), or none. Each verdict carries its `Reference branch:` line.
4. **Promotions.** For each: name, home, interface, evidence, and the governance it has to enforce.
   Add an explicit "do not promote" list.

## Audit prompts (phase 9)

**Accuracy audit.** Re-derive every count from the working files. Open every quoted range and check
its first and last lines are the ones cited. For every "missing" or "partial" verdict, read the
default branch again and check for commits to candidate modules in the last two weeks.

**Design audit.** For every proposed tool, state its one job, its input and output, its exit codes,
and which layer it lives in. Flag a proposal that bundles several jobs behind switches, or that
mixes policy (what this project wants) into mechanism (how the tool works). A consolidation that
produces one large tool has moved the problem, not solved it.

## What is written down

- Excerpts and tables that go into a tracked document are screened first for credentials, hosts,
  addresses and home-directory paths.
- A scheduled job that runs from a checkout on a branch other than the default is recorded as a
  finding in its own right.
- The consolidation plan gives each tool its own steps, its list of callers, and the scripts it
  retires.
<!-- AGENTTEAMS:END script_consolidation -->

## Placement Decision Rule

> ⚙️ **USER-EDITABLE** — record where each kind of tool belongs in this project and how it reaches
> the places that run it. The questions below are a starting point; replace them with this
> project's answers. `--update` keeps this section as the project wrote it.

For each proposed tool, answer:

- Who uses it: this repository only, or two or more?
- Where does it run: a laptop schedule, a container image, a serverless function, a host?
- How does a new version reach each of those places, and how long does that take?
- Does it change production data? If so, which clearance does it have to record?
- Is it operator policy (thresholds, schedules, recipients) or mechanism?
- Which repository's default branch is its home, and is the local checkout of that repository
  current?
