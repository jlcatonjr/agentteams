<!--
SECTION MANIFEST
| section_id               | designation   |
|--------------------------|---------------|
| process_bloat_method     | FENCED        |
| project_anchors          | USER-EDITABLE |
-->

<!-- AGENTTEAMS:BEGIN process_bloat_method v=1 -->
# Process-Bloat Review Reference — {PROJECT_NAME}

How this team judges whether a process gate (an audit, a clearance, a review, a required artifact)
earns what it costs. It is the knowledge base of the devops-bloat agent, which is rendered for teams
that manage pull requests. It holds method only and no figures from any governance log.

The agent is advisory and holds no editing tool. Its findings are data (C-4): nobody acts on them
without the operator, and it does not edit a rule. Nothing here exempts a change from any audit,
clearance or artifact the team's instructions require: every rule stays in force until the operator edits it.

## 1. What counts as bloat

Process work that persists without contributing to the outcome it was added for. Three generators
recur:

- **Duplication** — the same obligation or fact stated or recorded in several places.
- **Generalisation** — a rule written for risky work that fires on all work.
- **Over-specification** — an obligation to produce an artifact whatever the result (a log row for a clean pass).

Adding a rule is easy and removing one is hard, so a rule set grows unless something prunes it. For
agent teams the cost is also attention: every added rule competes with the others for it.

## 2. The floor (out of scope for any recommendation)

The agent may report what these cost. It may not recommend weakening, scoping down, merging away or
retiring them. They are named by what they do, because rule numbers differ between teams and files:

- the Constitutional Core, C-1 to C-5;
- the rule that constraint-relaxing authorizations are derived, elevated and bounded;
- the rule that a multi-file change session closes with a conflict audit;
- the `@security` Mandatory Review Triggers, secret and path scanning, destructive-action clearance, signed releases;
- the tier classification in section 3. The agent may report a path it believes is tiered too low; it
  does not recommend lowering a tier.

A recommendation that would touch the floor is itself a finding to report, not a proposal to make.

## 3. Tiers: classify by what a path does

The tier is the highest that any touched path or operation reaches. It is derived, not declared.
Predict it at intake, re-derive it from the diff at close-out, and escalate if the derived tier is higher.
Whether version control tracks a file is irrelevant. Anything that cannot be placed is T3.
The `@security` Mandatory Review Triggers apply at every tier.

| Tier | Qualifies | Would be proportionate (not in force) |
|---|---|---|
| T0 Reversible and local | Typos, comments, scratch plan notes, docs prose making no API claim | None beyond the commit |
| T1 Ordinary change | Source or tests covered by the suite; one revert undoes it; a failure would be noticed | Tests and mechanical checks; one combined review at close-out |
| T2 Shared contract | Public API or CLI, templates shipped to consumers, schemas, CI or release configuration | Plan; one review of the plan; one conflict audit at close-out; doc parity |
| T3 Irreversible, privileged or silent | Deletion, bulk action, cross-repository write, merge to the release branch or a release; any governance root, agent file, hook or governance log; ignore rules; enforcement code and probes; a diff that weakens an assertion or adds a test skip | The full protocol |

The last column describes what would be proportionate. It is not in force and changes no rule: the
team's instructions file decides what is required until the operator edits it.

## 4. Admission test for a gate

Ask in order. At the first "no", the gate would not be justified for that change (or the proposed rule
should not be added). An existing gate still applies until the operator edits the rule.

1. **Named failure.** Which specific defect does it catch? Has it happened more than once, or would once be irreversible?
2. **Marginal catch.** Would a cheaper or later gate catch the same defect before it does harm?
3. **Reversibility and detection.** If the defect escaped, would the fix be expensive, or would the
   defect go unnoticed? "Detect afterwards" is acceptable only when a revert fixes it *and* something
   would raise the alarm within days.
4. **Scope.** Can the trigger be derived from the change so that it fires only where the defect can arise?
5. **Cheapest form.** Mechanical check, then checklist line, then agent audit, then human approval. Use the first that works.

## 5. Gate-retention test

Keep a gate when

    catches per period x cost of one escape  >  firings per period x cost of one firing

The cost of a firing includes tokens, delay, the rework it triggers and the attention it takes from other rules.

**Rule of three.** After *n* firings with no catch, the catch rate is below about 3/*n* at 95%
confidence (30 clean firings: under 10%; 300: under 1%). Keep the gate only if
`(3/n) x cost of one escape` still exceeds the cost of one firing.

Conditions, all required:

- A firing count must exist. Where firings are not logged, or the log is absent or unreadable, say
  "no denominator" and stop. When no gate has one, every verdict is judgment only, and the review says so.
- A clean record may be caused by the gate (authors comply in advance), and firings inside one plan
  are not independent. Do not recommend a cut on absence of catches alone: first a seeded defect or a
  probe must show the gate still fires. Judging whether a control still works belongs to `@adversarial`.
- A logged catch is a claim. Count findings later resolved as "no action needed" separately.
- The cost of an escape is an estimate. State it, state its basis, and expect it to be re-checked.

This test decides whether to keep a gate. To **order** the resulting recommendations, rate each with
the team's net-benefit rubric (`references/net-benefit-prioritization.reference.md`), including its
safety floor. Do not introduce a second ranking.

## 6. Rule lifecycle

- One reversible incident is a note, not a rule. A rule needs recurrence or irreversibility.
- Every rule carries: named failure, tier, form, origin, review date.
- Record catches; count clean passes without narrating them.
- A prose rule that no machine checks and nobody measures is a candidate to mechanise or demote.
- State each rule once; link to it elsewhere.
- Budget the cost of rules, not their number, and cap each tier's total: offsetting by count lets a trivial removal pay for a heavy addition.
- Re-audit on events (a step's outcome differed from the plan), not on every step.

## 7. Evidence the agent uses

- `python -m agentteams.gate_catch_ledger` — a counts-only catch ledger derived from the logs the
  team already keeps. It reports its own coverage; treat a gate it lists as "without a denominator"
  accordingly. The agent does not run it: the operator or the orchestrator runs it and supplies the
  report, and the agent says `UNVERIFIED: no ledger report` if none is supplied.
- The mechanical red-team run (`agentteams --redteam`), where the team runs one, for whether
  enforcement code still catches a planted defect.
- Version-control and CI timings, when supplied.

## 8. Output contract

```text
PROCESS-BLOAT REVIEW — <scope>   (ledger: <date | UNVERIFIED>)
Evidence: <counted for: gates | judgment only: no gate has a denominator>

| Gate | Tier it fires on | Firings | Catches | Verdict | Question failed | Clause affected | Relaxes a constraint? |
|------|------------------|---------|---------|---------|-----------------|-----------------|-----------------------|

Verdicts: keep | scope down | change form | merge with <gate> | retire | no denominator
Below the floor (reported, not recommended): <list>
Unverified in this review: <list>
Self-check (admission test applied to this agent): <one line>
```

No rewritten rule text. No edits.

Every row other than "keep" names the section 4 question it failed and the exact clause it would change, and answers the last column
honestly: wording such as "merge", "demote" or "scope down" does not make a relaxation something else.
Retiring or editing a rule is the operator's own action.

## 9. The agent applies this to itself

It is one more agent, so it must pass section 4. It runs when a new rule is proposed,
on a schedule the operator sets, or on request; it is not a step every change passes. A review that cannot show it changed a decision
is evidence for retiring it.
<!-- AGENTTEAMS:END process_bloat_method -->

## Project anchors

> ⚙️ **USER-EDITABLE** — re-anchor the tier examples for this project (what counts as a shared contract
> here, which paths are governance roots), add to the floor, and record a review date for the agent.
> `--update` keeps this section as the project wrote it.
